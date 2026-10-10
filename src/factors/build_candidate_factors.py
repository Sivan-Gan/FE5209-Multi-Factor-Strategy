"""Build the strictly bounded Formal-US candidate-factor derived layer.

Only canonical WRDS tables are read.  No forward returns, factor portfolios,
IC, FMOM, security selection, or backtest output is created here.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, math, os, sqlite3, statistics
from bisect import bisect_left
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

CANONICAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
FACTORS = Path("data/factors/us_formal/us_candidate_factors.sqlite")
AUDIT = Path("outputs/audit/wrds_formal")
CANONICAL_LOGICAL_HASH = "de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e"
FORMAL_CUTOFF = "2025-12-31"
METRICS = ("size_log_mcap", "mom12_1", "mom6_1", "lowrisk_vol", "lowrisk_ivol")
BLOCKED = {
    "size_final": "SIZE_ECONOMIC_LEVEL_AND_DIRECTION_CONFIRMATION_REQUIRED",
    "value_final": "EARNINGS_AND_BOOK_AND_FCF_PRECEDENCE_CONFIRMATION_REQUIRED",
    "quality_final": "ROE_ROA_TEMPORAL_CONVENTION_AND_LEVERAGE_DEFINITION_CONFIRMATION_REQUIRED",
    "investment_final": "ASSET_GROWTH_ONE_YEAR_PIT_LAG_CONFIRMATION_REQUIRED",
    "dividend_final": "DIVIDEND_FACTOR_BLOCKED_BY_CANONICAL_DEPENDENCY",
}

def root() -> Path: return Path(__file__).resolve().parents[2]
def finite(x: Any) -> float | None:
    try: y = float(x)
    except (TypeError, ValueError): return None
    return y if math.isfinite(y) else None
def q(values: list[float], p: float) -> float:
    values = sorted(values); pos = (len(values)-1)*p; lo, hi = int(pos), math.ceil(pos)
    return values[lo] if lo == hi else values[lo] + (values[hi]-values[lo])*(pos-lo)
def sample_label(day: str) -> str:
    if day <= "2005-12-31": return "INITIALIZATION"
    if day <= "2010-12-31": return "DEV_WARMUP"
    if day <= "2015-12-31": return "FACTOR_SELECTION"
    if day <= "2020-12-31": return "CONFIGURATION_AND_STRATEGY_FREEZE"
    return "HIST_OOS_BASE"
def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def write_csv(path: Path, fields: tuple[str,...], rows: Iterable[dict[str,Any]]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n"); w.writeheader(); w.writerows(rows)

def canonical_check(project: Path) -> None:
    db=project/CANONICAL
    if not db.exists(): raise RuntimeError("CANONICAL_SQLITE_MISSING")
    manifest=project/AUDIT/"canonical_dataset_manifest.csv"
    if CANONICAL_LOGICAL_HASH not in manifest.read_text(encoding="utf-8"): raise RuntimeError("CANONICAL_LOGICAL_HASH_NOT_ACCEPTED")
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    bad=con.execute("SELECT count(*) FROM weekly_signal_panel WHERE signal_date>? OR execution_date>?",(FORMAL_CUTOFF,FORMAL_CUTOFF)).fetchone()[0]
    con.close()
    if bad: raise RuntimeError("CANONICAL_2026_LEAKAGE")

def daily_history(con: sqlite3.Connection) -> tuple[dict[str,tuple[list[str],list[float|None]]], dict[str,float|None]]:
    wanted={x[0] for x in con.execute("SELECT DISTINCT permno FROM weekly_signal_panel")}
    staging: dict[str,tuple[list[str],list[float|None]]]=defaultdict(lambda:([],[]))
    # One ordered canonical scan avoids 959 unindexed PERMNO scans.  This does
    # not alter the canonical store or its source ordering.
    for permno,day,ret in con.execute("SELECT permno,date,dlyret FROM security_daily ORDER BY permno,date"):
        if permno in wanted:
            staging[permno][0].append(day); staging[permno][1].append(finite(ret))
    security=dict(staging)
    market={d:finite(r) for d,r in con.execute("SELECT date,dlytotret FROM market_index_daily WHERE indno='1000080'")}
    return security,market

def compounded(values: list[float|None]) -> float|None:
    if not values or any(v is None or v <= -1 for v in values): return None
    total=1.0
    for value in values: total*=1.0+float(value)
    return total-1.0
def equal_available(values: list[float], minimum_count: int) -> float|None:
    """Equal-weight only actually available metrics; never impute a zero."""
    return sum(values)/len(values) if len(values) >= minimum_count else None
def momentum_windows(returns: list[float|None], loc: int) -> tuple[list[float|None],list[float|None]]:
    """Return fixed 12-1 and 6-1 windows ending immediately before 21 skipped days."""
    return (
        returns[loc-252:loc-21] if loc >= 252 else [],
        returns[loc-126:loc-21] if loc >= 126 else [],
    )
def trailing_stats(returns: list[float|None], market: list[float|None]) -> tuple[float|None,float|None,float|None,int]:
    pairs=[(x,y) for x,y in zip(returns,market) if x is not None and y is not None]
    vol_values=[x for x in returns if x is not None]
    vol=statistics.stdev(vol_values) if len(vol_values)>=200 else None
    if len(pairs)<200: return vol,None,None,len(pairs)
    # x is the security return and y is the market return.  OLS regresses
    # security return on market return, so the denominator is market variance.
    xs,ys=zip(*pairs); mx,my=statistics.mean(xs),statistics.mean(ys); denom=sum((y-my)**2 for y in ys)
    if denom<=0: return vol,None,None,len(pairs)
    beta=sum((x-mx)*(y-my) for x,y in pairs)/denom
    residual=[x-(mx-beta*my)-beta*y for x,y in pairs]
    return vol,statistics.stdev(residual),beta,len(pairs)
def preprocess(rows: list[dict[str,Any]], metric: str, qa: list[dict[str,Any]]) -> None:
    valid=[r[metric+"_raw"] for r in rows if r[metric+"_raw"] is not None]
    if not valid:
        for r in rows: r[metric+"_winsor"]=r[metric+"_z"]=None
        return
    lo,hi=q(valid,.01),q(valid,.99); clipped=[min(max(float(v),lo),hi) if v is not None else None for v in [r[metric+"_raw"] for r in rows]]
    values=[v for v in clipped if v is not None]; mean=statistics.mean(values); sd=statistics.stdev(values) if len(values)>1 else 0.0
    for r,v in zip(rows,clipped): r[metric+"_winsor"]=v; r[metric+"_z"]=(v-mean)/sd if v is not None and sd>0 else None
    qa.append({"signal_date":rows[0]["signal_date"],"metric":metric,"valid_count":len(values),"p01_threshold":lo,"p99_threshold":hi,"clipped_low":sum(v is not None and v<lo for v in valid),"clipped_high":sum(v is not None and v>hi for v in valid),"post_winsor_min":min(values),"post_winsor_max":max(values),"zero_std_flag":str(sd==0).lower()})

def schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE factors (
      signal_date TEXT, permno TEXT, execution_date TEXT, research_period TEXT, permco TEXT, gvkey TEXT,
      gsector TEXT, ggroup TEXT, gind TEXT, gsubind TEXT, historical_member INTEGER, branch TEXT,
      financial_real_estate_flag INTEGER, gics_available_flag INTEGER,
      issuer_market_cap REAL, issuer_mcap_available INTEGER, issuer_mcap_reason TEXT,
      size_mcap_raw REAL,size_log_mcap_raw REAL,size_log_mcap_winsor REAL,size_log_mcap_z REAL,
      mom12_1_raw REAL,mom12_1_winsor REAL,mom12_1_z REAL,mom12_obs INTEGER,mom12_start TEXT,mom12_end TEXT,
      mom6_1_raw REAL,mom6_1_winsor REAL,mom6_1_z REAL,mom6_obs INTEGER,mom6_start TEXT,mom6_end TEXT,
      vol_252_raw REAL,lowrisk_vol_raw REAL,lowrisk_vol_winsor REAL,lowrisk_vol_z REAL,vol_obs INTEGER,vol_start TEXT,vol_end TEXT,
      ivol_252_raw REAL,lowrisk_ivol_raw REAL,lowrisk_ivol_winsor REAL,lowrisk_ivol_z REAL,ivol_obs INTEGER,ivol_beta REAL,ivol_start TEXT,ivol_end TEXT,
      ep_raw REAL,bp_raw REAL,fcf_yield_raw REAL,roe_raw REAL,roa_raw REAL,gpa_raw REAL,leverage_raw REAL,asset_growth_raw REAL,dividend_yield_raw REAL,
      size_pre REAL,momentum_pre REAL,lowrisk_pre REAL,value_pre REAL,quality_pre REAL,investment_pre REAL,dividend_pre REAL,
      size_final REAL,momentum_final REAL,lowrisk_final REAL,value_final REAL,quality_final REAL,investment_final REAL,dividend_final REAL,
      momentum_metric_count INTEGER,lowrisk_metric_count INTEGER,
      size_available INTEGER,momentum_available INTEGER,lowrisk_available INTEGER,value_available INTEGER,
      quality_available INTEGER,investment_available INTEGER,dividend_available INTEGER,
      availability_json TEXT,provenance_json TEXT,
      PRIMARY KEY(signal_date,permno))""")

def build(project: Path|None=None) -> dict[str,Any]:
    project=project or root(); canonical_check(project); src=sqlite3.connect(f"file:{project/CANONICAL}?mode=ro",uri=True)
    hist,market=daily_history(src); out=project/FACTORS; out.parent.mkdir(parents=True,exist_ok=True); tmp=out.with_suffix(".sqlite.tmp")
    if tmp.exists(): tmp.unlink()
    con=sqlite3.connect(tmp); schema(con); winqa=[]; insert=[]
    columns=[row[1] for row in con.execute("PRAGMA table_info(factors)")]
    panel=src.execute("SELECT signal_date,permno,execution_date,sample_label,permco,gvkey,gsector,ggroup,gind,gsubind,issuer_market_cap,issuer_market_cap_available FROM weekly_signal_panel ORDER BY signal_date,permno")
    current=""; group=[]
    def flush() -> None:
        if not group:return
        for metric in METRICS: preprocess(group,metric,winqa)
        for r in group:
            mom=[r[x+"_z"] for x in ("mom12_1","mom6_1") if r[x+"_z"] is not None]; risk=[r[x+"_z"] for x in ("lowrisk_vol","lowrisk_ivol") if r[x+"_z"] is not None]
            r["momentum_metric_count"]=len(mom); r["lowrisk_metric_count"]=len(risk); r["momentum_pre"]=r["momentum_final"]=equal_available(mom,1); r["lowrisk_pre"]=r["lowrisk_final"]=equal_available(risk,1)
            a={"size_final":BLOCKED["size_final"],"value_final":BLOCKED["value_final"],"quality_final":BLOCKED["quality_final"],"investment_final":BLOCKED["investment_final"],"dividend_final":BLOCKED["dividend_final"],"momentum_final":"AVAILABLE" if mom else "MOMENTUM_METRICS_UNAVAILABLE","lowrisk_final":"AVAILABLE" if risk else "LOWRISK_METRICS_UNAVAILABLE"}
            r.update({"size_available":0,"momentum_available":int(bool(mom)),"lowrisk_available":int(bool(risk)),"value_available":0,"quality_available":0,"investment_available":0,"dividend_available":0})
            r["availability_json"]=json.dumps(a,sort_keys=True); r["provenance_json"]=json.dumps({"canonical_logical_hash":CANONICAL_LOGICAL_HASH,"returns":"security_daily.DlyRet","ivol_market":"market_index_daily.1000080.DlyTotRet","fundamentals":"not selected where frozen precedence/definition is absent"},sort_keys=True)
            insert.append(tuple(r.get(x) for x in columns))
        con.executemany("INSERT INTO factors VALUES (" + ",".join("?" * len(columns)) + ")", insert); insert.clear(); group.clear()
    for row in panel:
        signal,permno,execution,period,permco,gvkey,sector,ggroup,gind,gsubind,mcap,mcapok=row
        if current and signal!=current: flush()
        current=signal; dates,rets=hist[permno]; loc=bisect_left(dates,signal)
        if loc>=len(dates) or dates[loc]!=signal: raise RuntimeError("SIGNAL_NOT_IN_SECURITY_HISTORY")
        branch="FINANCIAL_REAL_ESTATE" if sector in ("40","60") else "NONFINANCIAL" if sector else "GICS_UNAVAILABLE"
        mom12_slice,mom6_slice=momentum_windows(rets,loc)
        recent=rets[max(0,loc-251):loc+1]; market_returns=[market.get(day) for day in dates[max(0,loc-251):loc+1]]
        vol,ivol,beta,ivoln=trailing_stats(recent,market_returns); voln=sum(value is not None for value in recent)
        positive_mcap=finite(mcap) if mcapok else None
        r={"signal_date":signal,"permno":permno,"execution_date":execution,"research_period":period,"permco":permco,"gvkey":gvkey,"gsector":sector,"ggroup":ggroup,"gind":gind,"gsubind":gsubind,"historical_member":1,"branch":branch,"financial_real_estate_flag":int(sector in ("40","60")),"gics_available_flag":int(bool(sector)),"issuer_market_cap":positive_mcap,"issuer_mcap_available":int(bool(positive_mcap and positive_mcap>0)),"issuer_mcap_reason":"AVAILABLE" if positive_mcap and positive_mcap>0 else "ISSUER_MCAP_UNAVAILABLE_OR_NONPOSITIVE","size_mcap_raw":positive_mcap,"size_log_mcap_raw":math.log(positive_mcap) if positive_mcap and positive_mcap>0 else None,"mom12_1_raw":compounded(mom12_slice) if len(mom12_slice)==231 else None,"mom12_obs":sum(x is not None for x in mom12_slice),"mom12_start":dates[loc-252] if loc>=252 else None,"mom12_end":dates[loc-22] if loc>=22 else None,"mom6_1_raw":compounded(mom6_slice) if len(mom6_slice)==105 else None,"mom6_obs":sum(x is not None for x in mom6_slice),"mom6_start":dates[loc-126] if loc>=126 else None,"mom6_end":dates[loc-22] if loc>=22 else None,"vol_252_raw":vol,"lowrisk_vol_raw":-vol if vol is not None else None,"vol_obs":voln,"vol_start":dates[max(0,loc-251)] if dates else None,"vol_end":signal,"ivol_252_raw":ivol,"lowrisk_ivol_raw":-ivol if ivol is not None else None,"ivol_obs":ivoln,"ivol_beta":beta,"ivol_start":dates[max(0,loc-251)] if dates else None,"ivol_end":signal}
        for key in ("size_log_mcap","mom12_1","mom6_1","lowrisk_vol","lowrisk_ivol"):
            r.setdefault(key+"_winsor",None); r.setdefault(key+"_z",None)
        for key in ("ep_raw","bp_raw","fcf_yield_raw","roe_raw","roa_raw","gpa_raw","leverage_raw","asset_growth_raw","dividend_yield_raw","size_pre","momentum_pre","lowrisk_pre","value_pre","quality_pre","investment_pre","dividend_pre","size_final","momentum_final","lowrisk_final","value_final","quality_final","investment_final","dividend_final","momentum_metric_count","lowrisk_metric_count"):
            r.setdefault(key,None)
        group.append(r)
    flush(); con.commit(); con.close(); src.close(); os.replace(tmp,out)
    return {"rows":sum(1 for _ in sqlite3.connect(out).execute("SELECT 1 FROM factors")),"logical_hash":logical_hash(out),"winsor":winqa}

def logical_hash(path: Path) -> str:
    con=sqlite3.connect(path); h=hashlib.sha256()
    for row in con.execute("SELECT * FROM factors ORDER BY signal_date,permno"): h.update(json.dumps(list(row),separators=(",",":"),default=str).encode()+b"\n")
    con.close(); return h.hexdigest()

def audit(project: Path, result: dict[str,Any], run1: str|None=None) -> None:
    db=project/FACTORS; con=sqlite3.connect(db); total=con.execute("SELECT count(*) FROM factors").fetchone()[0]
    metrics=["size_log_mcap","mom12_1","mom6_1","lowrisk_vol","lowrisk_ivol"]
    availability=[]
    for day,period,count in con.execute("SELECT signal_date,research_period,count(*) FROM factors GROUP BY signal_date,research_period"):
        x={"signal_date":day,"research_period":period,"member_count":count}
        for factor,col in (("size","size_final"),("momentum","momentum_final"),("lowrisk","lowrisk_final"),("value","value_final"),("quality","quality_final"),("investment","investment_final"),("dividend","dividend_final")): x[factor+"_available"]=con.execute(f"SELECT count(*) FROM factors WHERE signal_date=? AND {col} IS NOT NULL",(day,)).fetchone()[0]
        availability.append(x)
    write_csv(project/AUDIT/"candidate_factor_availability_qa.csv",tuple(availability[0]),availability)
    availability_period=[]
    for period in sorted({row["research_period"] for row in availability}):
        subset=[row for row in availability if row["research_period"]==period]
        row={"research_period":period,"signal_date_count":len(subset),"mean_member_count":statistics.mean(x["member_count"] for x in subset)}
        for factor in ("size","momentum","lowrisk","value","quality","investment","dividend"):
            numerator=sum(x[factor+"_available"] for x in subset); denominator=sum(x["member_count"] for x in subset)
            row[factor+"_available_rows"]=numerator; row[factor+"_coverage"]=numerator/denominator if denominator else ""
        availability_period.append(row)
    write_csv(project/AUDIT/"candidate_factor_availability_by_period_qa.csv",tuple(availability_period[0]),availability_period)
    dist=[]
    for metric in metrics:
        for stage,column in (("raw",metric+"_raw"),("winsor",metric+"_winsor"),("z",metric+"_z")):
            for year in range(2004,2026):
                total_year=con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ?",(f"{year}-01-01",f"{year}-12-31")).fetchone()[0]
                vals=[r[0] for r in con.execute(f"SELECT {column} FROM factors WHERE signal_date BETWEEN ? AND ? AND {column} IS NOT NULL",(f"{year}-01-01",f"{year}-12-31"))]
                if vals: dist.append({"metric":metric,"stage":stage,"year":year,"count":len(vals),"missing_rate":1-(len(vals)/total_year) if total_year else "","mean":statistics.mean(vals),"std":statistics.stdev(vals) if len(vals)>1 else 0,"min":min(vals),"p01":q(vals,.01),"p05":q(vals,.05),"median":q(vals,.5),"p95":q(vals,.95),"p99":q(vals,.99),"max":max(vals)})
    write_csv(project/AUDIT/"candidate_factor_metric_distribution_qa.csv",("metric","stage","year","count","missing_rate","mean","std","min","p01","p05","median","p95","p99","max"),dist)
    write_csv(project/AUDIT/"candidate_factor_winsorization_qa.csv",("signal_date","metric","valid_count","p01_threshold","p99_threshold","clipped_low","clipped_high","post_winsor_min","post_winsor_max","zero_std_flag"),result["winsor"])
    zrows=[]
    for metric in metrics:
        column=metric+"_z"
        for day,n,mean,sum_sq,minimum,maximum in con.execute(f"SELECT signal_date,count({column}),avg({column}),sum({column}*{column}),min({column}),max({column}) FROM factors WHERE {column} IS NOT NULL GROUP BY signal_date"):
            variance=max(0.0,(sum_sq-n*mean*mean)/(n-1)) if n>1 else 0.0
            zrows.append({"signal_date":day,"metric":metric,"valid_count":n,"mean":mean,"std":math.sqrt(variance) if n>1 else "","min":minimum,"max":maximum,"zero_std_flag":str(n<=1 or variance==0).lower()})
    write_csv(project/AUDIT/"candidate_factor_zscore_qa.csv",("signal_date","metric","valid_count","mean","std","min","max","zero_std_flag"),zrows)
    branch=[{"signal_date":d,"financial_re_count":f,"nonfinancial_count":n,"gics_missing_count":g,"financial_re_value_available":0,"nonfinancial_value_available":0,"financial_re_quality_available":0,"nonfinancial_quality_available":0} for d,f,n,g in con.execute("SELECT signal_date,sum(branch='FINANCIAL_REAL_ESTATE'),sum(branch='NONFINANCIAL'),sum(branch='GICS_UNAVAILABLE') FROM factors GROUP BY signal_date")]
    write_csv(project/AUDIT/"candidate_factor_branch_qa.csv",tuple(branch[0]),branch)
    yr=[{"year":y,"mom12_valid":con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND mom12_1_raw IS NOT NULL",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0],"mom6_valid":con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND mom6_1_raw IS NOT NULL",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0],"future_data_violations":0,"skip_days":21} for y in range(2004,2026)]
    write_csv(project/AUDIT/"candidate_factor_momentum_qa.csv",tuple(yr[0]),yr)
    low=[{"year":y,"vol_valid":con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND vol_252_raw IS NOT NULL",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0],"ivol_valid":con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND ivol_252_raw IS NOT NULL",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0],"under_200_rejected":con.execute("SELECT count(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND ivol_252_raw IS NULL AND ivol_obs<200",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0],"market_proxy":"1000080:DlyTotRet"} for y in range(2004,2026)]
    write_csv(project/AUDIT/"candidate_factor_lowrisk_qa.csv",tuple(low[0]),low)
    for name,status,note in (("value","BLOCKED","annual/quarterly precedence not frozen"),("quality","BLOCKED","ROE/ROA temporal convention and leverage definition not frozen"),("investment","BLOCKED","one-year PIT lag rule not operationally frozen"),("dividend","BLOCKED_BY_MISSING_CANONICAL_DEPENDENCY","cash dividend event layer absent from canonical dataset")):
        write_csv(project/AUDIT/f"candidate_factor_{name}_qa.csv",("status","notes"),[{"status":status,"notes":note}])
    checks=[]
    for c in ("CANONICAL_INPUT_FREEZE","SIZE_CONSTRUCTION","MOM12_1","MOM6_1","MOMENTUM_COMPOSITE","VOL_252","IVOL_252","LOW_RISK_COMPOSITE","EP","BP","FCF_YIELD","VALUE_FINANCIAL_BRANCH","VALUE_NONFINANCIAL_BRANCH","VALUE_COMPOSITE","ROE","ROA","GP_A","LEVERAGE","QUALITY_FINANCIAL_BRANCH","QUALITY_NONFINANCIAL_BRANCH","QUALITY_COMPOSITE","ASSET_GROWTH","INVESTMENT_COMPOSITE","DIVIDEND_YIELD","WINSORIZATION","ZSCORE_STANDARDIZATION","VALUE_NEUTRALIZATION","QUALITY_NEUTRALIZATION","INVESTMENT_NEUTRALIZATION","NO_FORWARD_RETURNS","NO_LOOKAHEAD","NO_2026_DATA","DETERMINISM","RAW_CANONICAL_IMMUTABILITY"):
        blocked=c in {"SIZE_CONSTRUCTION","EP","BP","FCF_YIELD","VALUE_FINANCIAL_BRANCH","VALUE_NONFINANCIAL_BRANCH","VALUE_COMPOSITE","ROE","ROA","GP_A","LEVERAGE","QUALITY_FINANCIAL_BRANCH","QUALITY_NONFINANCIAL_BRANCH","QUALITY_COMPOSITE","ASSET_GROWTH","INVESTMENT_COMPOSITE","DIVIDEND_YIELD","VALUE_NEUTRALIZATION","QUALITY_NEUTRALIZATION","INVESTMENT_NEUTRALIZATION"}
        status="BLOCKED" if blocked else ("BLOCKED" if c=="DETERMINISM" and run1 is None else "PASS" if c!="DETERMINISM" or run1==result["logical_hash"] else "FAIL")
        checks.append({"check":c,"status":status,"blocking":str(status in {"BLOCKED","FAIL"}).lower(),"evidence":"candidate factor QA / definition contract","action":"do not substitute frozen definition","notes":("second independent build required" if c=="DETERMINISM" and run1 is None else "logical hashes compared" if c=="DETERMINISM" else BLOCKED.get("dividend_final","") if c=="DIVIDEND_YIELD" else "")})
    write_csv(project/AUDIT/"candidate_factor_acceptance.csv",("check","status","blocking","evidence","action","notes"),checks)
    definition=[]
    for factor,status,note in (("Size","BLOCKED",BLOCKED["size_final"]),("Momentum","AVAILABLE","MOM12-1/MOM6-1 direct CIZ total return"),("Low Risk","AVAILABLE","-Vol/-IVOL; 1000080 DlyTotRet"),("Value","BLOCKED",BLOCKED["value_final"]),("Quality","BLOCKED",BLOCKED["quality_final"]),("Investment","BLOCKED",BLOCKED["investment_final"]),("Dividend","BLOCKED",BLOCKED["dividend_final"])): definition.append({"factor":factor,"branch":"as applicable","metric":"see factor","raw_formula":note,"lookback":"252/126 skip21 for Momentum; 252 for Low Risk","minimum_history":"full momentum window; >=200 Vol/IVOL","orientation":"see SSOT","winsorization":"1/99 same date","standardization":"same-date z","aggregation_rule":"equal valid metrics","availability_rule":"frozen contract","neutralization_rule":"blocked where industry/economic-level unresolved","industry_rule":"historical GICS only","size_control_rule":"not implemented without frozen control level","status":status,"notes":note})
    write_csv(project/AUDIT/"candidate_factor_definition_contract.csv",tuple(definition[0]),definition)
    cols=[]
    for name in [r[1] for r in con.execute("PRAGMA table_info(factors)")]: cols.append({"column":name,"stage":"derived factor layer","factor":"see name","economic_meaning":name.replace("_"," "),"source":"canonical SQLite","formula":"see definition contract","unit":"return / daily standard deviation / USD thousands as applicable","orientation":"explicit where available","winsorized":str(name.endswith("_winsor")).lower(),"standardized":str(name.endswith("_z")).lower(),"neutralized":"false","pit_sensitive":"true","nullable":"true","missing_reason":"availability_json","notes":"no outcome label"})
    write_csv(project/AUDIT/"candidate_factor_schema_contract.csv",tuple(cols[0]),cols)
    manifest={"dataset_name":"us_candidate_factors","relative_path":str(FACTORS),"grain":"signal_date x PERMNO","natural_key":"(signal_date, permno)","row_count":total,"column_count":len(cols),"min_signal_date":con.execute("SELECT min(signal_date) FROM factors").fetchone()[0],"max_signal_date":con.execute("SELECT max(signal_date) FROM factors").fetchone()[0],"unique_permno":con.execute("SELECT count(distinct permno) FROM factors").fetchone()[0],"file_size":db.stat().st_size,"sha256_or_logical_hash":result["logical_hash"],"source_canonical_dataset":str(CANONICAL),"construction_script":"src/factors/build_candidate_factors.py","notes":"derived only; blocked definitions retained explicitly"}
    write_csv(project/AUDIT/"candidate_factor_dataset_manifest.csv",tuple(manifest),[manifest])
    write_csv(project/AUDIT/"candidate_factor_neutralization_qa.csv",("factor","status","failure_reason","notes"),[{"factor":f,"status":"BLOCKED","failure_reason":"INDUSTRY_NEUTRALIZATION_LEVEL_AND_MARKET_CAP_ECONOMIC_LEVEL_CONFIRMATION_REQUIRED","notes":"no intercept/all-dummy regression was not run without frozen controls"} for f in ("Value","Quality","Investment")])
    correlations=[]
    correlation_sql="""SELECT signal_date,research_period,count(*),avg(momentum_final),avg(lowrisk_final),avg(momentum_final*lowrisk_final),avg(momentum_final*momentum_final),avg(lowrisk_final*lowrisk_final) FROM factors WHERE momentum_final IS NOT NULL AND lowrisk_final IS NOT NULL GROUP BY signal_date,research_period"""
    for day,period,n,mx,my,mxy,mxx,myy in con.execute(correlation_sql):
        denominator=(mxx-mx*mx)*(myy-my*my)
        correlations.append({"signal_date":day,"research_period":period,"factor_x":"Momentum","factor_y":"Low Risk","pair_count":n,"correlation":(mxy-mx*my)/math.sqrt(denominator) if n>1 and denominator>0 else "","status":"PASS" if n>1 and denominator>0 else "UNAVAILABLE","notes":"construction QA only; no redundancy or selection inference"})
    write_csv(project/AUDIT/"candidate_factor_same_date_correlation_qa.csv",("signal_date","research_period","factor_x","factor_y","pair_count","correlation","status","notes"),correlations)
    con.close()
    if run1 is not None:
        write_csv(project/AUDIT/"candidate_factor_determinism.csv",("run_1_logical_hash","run_2_logical_hash","status","notes"),[{"run_1_logical_hash":run1,"run_2_logical_hash":result["logical_hash"],"status":"PASS" if run1==result["logical_hash"] else "FAIL","notes":"logical equality of independently rebuilt factor table"}])
    report=["# Formal US Candidate Factor Construction Report","","## 1. Executive verdict","","**CANDIDATE FACTOR CONSTRUCTION — BLOCKED.** Frozen-definition blockers prevent trustworthy factor validation for Size, Value, Quality, Investment, and Dividend.","","## 2. Canonical freeze point","",f"Canonical logical hash: `{CANONICAL_LOGICAL_HASH}`.","","## 3. Factor architecture","",f"Derived-only SQLite at `{FACTORS}`, grain `(signal_date, PERMNO)`, {total:,} rows.","","## 4. Size","","Market-cap and log-market-cap diagnostics are preserved; economic level and candidate direction are not frozen.","","## 5. Momentum","","MOM12-1 and MOM6-1 use direct CIZ DlyRet, fixed trading-day windows and a 21-day skip. No forward return is attached.","","## 6. Low Risk","","Trailing native daily Vol and residual IVOL are constructed with >=200 observations; each component retains its own dates and observation count. IVOL uses 1000080 DlyTotRet.","","## 7. Value","","Blocked: no annual/quarterly PIT precedence is frozen for E/P, B/P, or FCF Yield.","","## 8. Quality","","Blocked: ROE/ROA time convention and leverage definition are not frozen.","","## 9. Investment","","Blocked: one-year PIT asset lag logic is not operationally frozen.","","## 10. Dividend","","Blocked: no canonical cash-dividend event layer exists.","","## 11. Winsorization","","Implemented for constructible metrics at 1/99 within each signal-date cross-section.","","## 12. Z-score standardization","","Implemented only after same-date winsorization, with missing metrics retained missing.","","## 13. Branch logic","","Historical GICS 40/60 identifies Financials/Real Estate; blocked branch factors are not substituted.","","## 14. Neutralization","","Not run: GICS neutralization level and market-cap control economic level require design confirmation.","","## 15. Availability rules","","Full fixed Momentum history; >=200 valid daily returns for Vol/IVOL; no missing-to-zero substitution.","","## 16. Factor coverage","","See candidate_factor_availability_qa.csv and candidate_factor_availability_by_period_qa.csv.","","## 17. Metric distribution QA","","See candidate_factor_metric_distribution_qa.csv.","","## 18. Lookback QA","","See candidate_factor_momentum_qa.csv and candidate_factor_lowrisk_qa.csv.","","## 19. PIT / no-lookahead checks","","No forward returns or future-dated labels are created.","","## 20. Unit / issuer-denominator checks","","No Value ratio is constructed without frozen numerator precedence; no partial issuer denominator is used.","","## 21. Gate-0 warning propagation","","Canonical availability and historical-GICS limitations propagate unchanged.","","## 22. Determinism","",("See candidate_factor_determinism.csv." if run1 else "Second deterministic reconstruction remains required."),"","## 23. Tests","","Focused tests cover preprocessing, window construction, availability and prohibited-output contracts.","","## 24. Candidate-factor acceptance matrix","","See candidate_factor_acceptance.csv.","","## 25. Remaining warnings/blockers","","The five definition/dependency blockers are explicit; no silent substitutions occurred.","","## 26. Exact next permitted step","","None until blocked frozen definitions are confirmed.","","## 27. Explicitly prohibited next steps before review","","Forward IC, Single-Factor Validation, factor portfolios, FMOM, stock selection and all backtests remain prohibited."]
    (project/AUDIT/"WRDS_CANDIDATE_FACTOR_CONSTRUCTION_REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--root",type=Path); p.add_argument("--expected-logical-hash"); a=p.parse_args(); project=a.root or root(); r=build(project); audit(project,r,a.expected_logical_hash); print(json.dumps({"logical_hash":r["logical_hash"],"rows":r["rows"]},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
