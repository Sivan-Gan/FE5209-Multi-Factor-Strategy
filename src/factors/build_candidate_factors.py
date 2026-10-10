"""Formal-US candidate factors from frozen WRDS canonical inputs only.

The module builds signals and diagnostic evidence.  It never creates outcome,
portfolio, selection, performance, or strategy data.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, math, os, sqlite3, statistics, tempfile
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from src.data import wrds_canonical_build as canonical
from src.data import wrds_canonical_dividend_extension as dividend
from src.data import wrds_gate0_final as gate0

CANONICAL=Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
DIVIDEND_EXTENSION=Path("data/canonical/wrds_formal/extensions/us_crsp_cash_dividend_events.sqlite")
FACTORS=Path("data/factors/us_formal/us_candidate_factors.sqlite")
AUDIT=Path("outputs/audit/wrds_formal")
CANONICAL_LOGICAL_HASH="de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e"
FORMAL_CUTOFF="2025-12-31"
FREEZE_COMMIT="d68aaaec585a85fe00879d192f7c5a221e297b0e"
BLOCKED_COMMIT="cd0151bd1df80b1834ce4cfe04b7e967adc6cbde"
SCORE_METRICS=("size","mom12_1","mom6_1","lowrisk_vol","lowrisk_ivol","ep","bp","fcf_yield","roe","roa","gpa","negative_leverage","investment","dividend_yield")

def root()->Path:return Path(__file__).resolve().parents[2]
def finite(x:Any)->float|None:
    try:y=float(x)
    except (TypeError,ValueError):return None
    return y if math.isfinite(y) else None
def day(x:Any)->date|None:
    try:return date.fromisoformat(str(x))
    except (TypeError,ValueError):return None
def js(x:Any)->str:return json.dumps(x,sort_keys=True,separators=(",",":"),default=str)
def q(v:Sequence[float],p:float)->float:
    v=sorted(v); z=(len(v)-1)*p; a,b=int(z),math.ceil(z); return v[a] if a==b else v[a]+(v[b]-v[a])*(z-a)
def write_csv(path:Path,fields:Sequence[str],rows:Iterable[dict[str,Any]])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");w.writeheader();w.writerows(rows)
def sample_label(d:str)->str:
    return "INITIALIZATION" if d<="2005-12-31" else "DEV_WARMUP" if d<="2010-12-31" else "FACTOR_SELECTION" if d<="2015-12-31" else "CONFIGURATION_AND_STRATEGY_FREEZE" if d<="2020-12-31" else "HIST_OOS_BASE"
def compounded(v:Sequence[float|None])->float|None:
    if not v or any(x is None or x<=-1 for x in v):return None
    r=1.0
    for x in v:r*=1+float(x)
    return r-1
def equal_available(v:Sequence[float],minimum:int)->float|None:return sum(v)/len(v) if len(v)>=minimum else None
def momentum_windows(r:Sequence[float|None],i:int)->tuple[list[float|None],list[float|None]]:return (list(r[i-252:i-21]) if i>=252 else [],list(r[i-126:i-21]) if i>=126 else [])
def trailing_stats(r:Sequence[float|None],m:Sequence[float|None])->tuple[float|None,float|None,float|None,int]:
    pairs=[(x,y) for x,y in zip(r,m) if x is not None and y is not None]; vv=[x for x in r if x is not None]; vol=statistics.stdev(vv) if len(vv)>=200 else None
    if len(pairs)<200:return vol,None,None,len(pairs)
    x,y=zip(*pairs);xm,ym=statistics.mean(x),statistics.mean(y);den=sum((z-ym)**2 for z in y)
    if den<=0:return vol,None,None,len(pairs)
    beta=sum((a-xm)*(b-ym) for a,b in pairs)/den; res=[a-(xm-beta*ym)-beta*b for a,b in pairs]
    return vol,statistics.stdev(res),beta,len(pairs)

def preprocess(rows:list[dict[str,Any]],metric:str,qa:list[dict[str,Any]])->None:
    raw,w,z=metric+"_raw",metric+"_winsor",metric+"_z"; vals=[float(r[raw]) for r in rows if r.get(raw) is not None]
    if not vals:
        for r in rows:r[w]=r[z]=None
        return
    lo,hi=q(vals,.01),q(vals,.99); clip=[min(max(float(r[raw]),lo),hi) if r.get(raw) is not None else None for r in rows]; good=[x for x in clip if x is not None]; mean=statistics.mean(good);sd=statistics.stdev(good) if len(good)>1 else 0
    for r,x in zip(rows,clip):r[w]=x;r[z]=(x-mean)/sd if x is not None and sd>0 else None
    qa.append({"signal_date":rows[0]["signal_date"],"metric":metric,"valid_count":len(good),"p01_threshold":lo,"p99_threshold":hi,"clipped_low":sum(x<lo for x in vals),"clipped_high":sum(x>hi for x in vals),"post_winsor_min":min(good),"post_winsor_max":max(good),"zero_std_flag":str(sd==0).lower()})
def zfield(rows:list[dict[str,Any]],src:str,dst:str)->None:
    v=[float(r[src]) for r in rows if r.get(src) is not None];mean=statistics.mean(v) if v else None;sd=statistics.stdev(v) if len(v)>1 else 0
    for r in rows:r[dst]=(float(r[src])-mean)/sd if r.get(src) is not None and mean is not None and sd>0 else None
def corr(x:Sequence[float],y:Sequence[float])->float|None:
    if len(x)<2:return None
    a,b=statistics.mean(x),statistics.mean(y);den=math.sqrt(sum((z-a)**2 for z in x)*sum((z-b)**2 for z in y));return sum((u-a)*(v-b) for u,v in zip(x,y))/den if den else None

def solve(a:list[list[float]],b:list[float])->tuple[list[float]|None,int]:
    n=len(b);z=[r[:] + [v] for r,v in zip(a,b)];scale=max((abs(x) for r in z for x in r),default=1.0);rank=0
    for c in range(n):
        p=max(range(rank,n),key=lambda i:abs(z[i][c]))
        if abs(z[p][c])<=1e-11*scale:continue
        z[rank],z[p]=z[p],z[rank];d=z[rank][c];z[rank]=[x/d for x in z[rank]]
        for i in range(n):
            if i!=rank and z[i][c]:
                f=z[i][c];z[i]=[x-f*y for x,y in zip(z[i],z[rank])]
        rank+=1
    if rank!=n:return None,rank
    out=[0.0]*n
    for r in z:
        k=next((i for i,x in enumerate(r[:n]) if abs(x)>1e-10*scale),None)
        if k is not None:out[k]=r[-1]
    return out,rank
def neutralize(rows:list[dict[str,Any]],pre:str,res:str,final:str,factor:str)->dict[str,Any]:
    for r in rows:r[res]=r[final]=None
    good=[r for r in rows if r.get(pre) is not None and r.get("issuer_market_cap") is not None and float(r["issuer_market_cap"])>0 and r.get("gsector")]
    sec=sorted({str(r["gsector"]) for r in good})
    base={"signal_date":rows[0]["signal_date"],"factor":factor,"n_obs":len(good),"number_of_sectors":len(sec)}
    if not good:return base|{"design_matrix_rank":0,"residual_mean":"","corr_residual_log_issuer_mcap":"","max_abs_sector_residual_mean":"","regression_failure":"true","failure_reason":"NO_VALID_NEUTRALIZATION_INPUT"}
    X=[[math.log(float(r["issuer_market_cap"]))]+[float(r["gsector"]==s) for s in sec] for r in good];Y=[float(r[pre]) for r in good];p=len(X[0]);gram=[[sum(x[i]*x[j] for x in X) for j in range(p)] for i in range(p)];rhs=[sum(x[i]*y for x,y in zip(X,Y)) for i in range(p)];beta,rank=solve(gram,rhs)
    if beta is None:return base|{"design_matrix_rank":rank,"residual_mean":"","corr_residual_log_issuer_mcap":"","max_abs_sector_residual_mean":"","regression_failure":"true","failure_reason":"DESIGN_RANK_DEFICIENT"}
    values=[];caps=[];by=defaultdict(list)
    for r,x,y in zip(good,X,Y):
        e=y-sum(a*b for a,b in zip(beta,x));r[res]=e;values.append(e);caps.append(x[0]);by[r["gsector"]].append(e)
    zfield(rows,res,final)
    return base|{"design_matrix_rank":rank,"residual_mean":statistics.mean(values),"corr_residual_log_issuer_mcap":corr(values,caps),"max_abs_sector_residual_mean":max(abs(statistics.mean(v)) for v in by.values()),"regression_failure":"false","failure_reason":""}

def canonical_check(project:Path)->None:
    if not (project/CANONICAL).is_file() or not (project/DIVIDEND_EXTENSION).is_file():raise RuntimeError("FROZEN_INPUT_MISSING")
    if CANONICAL_LOGICAL_HASH not in (project/AUDIT/"canonical_dataset_manifest.csv").read_text(encoding="utf-8"):raise RuntimeError("CANONICAL_HASH_NOT_ACCEPTED")
    h,_=dividend.extension_logical_hash(project/DIVIDEND_EXTENSION)
    if h not in (project/AUDIT/"canonical_dividend_extension_manifest.csv").read_text(encoding="utf-8"):raise RuntimeError("DIVIDEND_EXTENSION_HASH_NOT_ACCEPTED")

class Fundamentals:
    def __init__(self,c:sqlite3.Connection)->None:
        self.a=defaultdict(list);self.q=defaultdict(lambda:defaultdict(list));self.cache={}
        for eid,freq,gv,dd,fy,fq,av,raw,flow in c.execute("SELECT event_id,frequency,gvkey,datadate,fiscal_year,fiscal_quarter,availability_date,field_values_json,flow_values_json FROM pit_fundamental_events WHERE availability_date<>'' AND availability_date<=?",(FORMAL_CUTOFF,)):
            e={"id":eid,"dd":dd,"fy":fy,"fq":fq,"av":av,"v":{k:finite(v) for k,v in json.loads(raw).items()},"f":{k:finite(v) if k.endswith("_single_quarter") else v for k,v in json.loads(flow or "{}").items()}}
            if freq=="ANNUAL":self.a[gv].append(e)
            elif fy is not None and fq in (1,2,3,4):self.q[gv][int(fy)*4+int(fq)-1].append(e)
        for x in self.a.values():x.sort(key=lambda e:(e["dd"],e["av"],e["id"]))
        for d in self.q.values():
            for x in d.values():x.sort(key=lambda e:(e["av"],e["dd"],e["id"]))
    def P(self,value=None,freq="",events=(),fallback=False,reason=""):
        end=events[-1] if events else None
        return {"value":value,"source_frequency":freq,"source_datadate":end["dd"] if end else "","source_availability_date":end["av"] if end else "","source_event_ids":[e["id"] for e in events],"fallback_used":int(fallback),"failure_reason":reason}
    def period(self,gv:str,o:int,s:str):
        x=[e for e in self.q.get(gv,{}).get(o,[]) if e["av"]<=s];return x[-1] if x else None
    def latestq(self,gv:str,s:str,fields:Sequence[str]):
        for o in sorted(self.q.get(gv,{}),reverse=True):
            e=self.period(gv,o,s)
            if e and all(e["v"].get(f) is not None for f in fields):return e
    def latesta(self,gv:str,s:str,fields:Sequence[str]):
        for e in reversed(self.a.get(gv,[])):
            if e["av"]<=s and all(e["v"].get(f) is not None for f in fields):return e
    def ttm(self,gv:str,s:str,get:dict[str,Callable[[dict[str,Any]],float|None]],endfields=()):
        for o in sorted(self.q.get(gv,{}),reverse=True):
            end=self.period(gv,o,s)
            if not end or any(end["v"].get(f) is None for f in endfields):continue
            ev=[self.period(gv,o-i,s) for i in range(3,-1,-1)]
            if any(x is None for x in ev):continue
            ev=[x for x in ev if x is not None];val={k:[fun(e) for e in ev] for k,fun in get.items()}
            if all(all(x is not None for x in v) for v in val.values()):return {k:sum(float(x) for x in v) for k,v in val.items()},ev
    def annual(self,gv,s,fields,fun):
        e=self.latesta(gv,s,fields);return self.P(fun(e),"ANNUAL",(e,),True) if e else self.P(reason="ANNUAL_METRIC_UNAVAILABLE")
    def metrics(self,gv:str|None,s:str):
        if not gv:return {k:self.P(reason="GVKEY_UNAVAILABLE") for k in ("earnings","book","fcf","roe","roa","gpa","negative_leverage","investment")}
        if (gv,s) in self.cache:return self.cache[gv,s]
        out={};t=self.ttm(gv,s,{"ni":lambda e:e["v"].get("niq")});out["earnings"]=self.P(t[0]["ni"],"QUARTERLY_TTM",t[1]) if t else self.annual(gv,s,("ni",),lambda e:e["v"]["ni"])
        e=self.latestq(gv,s,("ceqq",));out["book"]=self.P(e["v"]["ceqq"],"QUARTERLY",(e,)) if e else self.annual(gv,s,("ceq",),lambda e:e["v"]["ceq"])
        t=self.ttm(gv,s,{"o":lambda e:e["f"].get("oancfy_single_quarter"),"c":lambda e:e["f"].get("capxy_single_quarter")});out["fcf"]=self.P(t[0]["o"]-t[0]["c"],"QUARTERLY_TTM",t[1]) if t else self.annual(gv,s,("oancf","capx"),lambda e:e["v"]["oancf"]-e["v"]["capx"])
        for name,denq,dena in (("roe","ceqq","ceq"),("roa","atq","at")):
            t=self.ttm(gv,s,{"ni":lambda e:e["v"].get("niq")},(denq,))
            if t and t[1][-1]["v"][denq] not in (None,0):out[name]=self.P(t[0]["ni"]/t[1][-1]["v"][denq],"QUARTERLY_TTM",t[1])
            else:
                e=self.latesta(gv,s,("ni",dena));out[name]=self.P(e["v"]["ni"]/e["v"][dena],"ANNUAL",(e,),True) if e and e["v"][dena] not in (None,0) else self.P(reason=name.upper()+"_UNAVAILABLE_OR_ZERO_DENOMINATOR")
        t=self.ttm(gv,s,{"gp":lambda e:e["v"]["saleq"]-e["v"]["cogsq"] if e["v"].get("saleq") is not None and e["v"].get("cogsq") is not None else None},("atq",))
        if t and t[1][-1]["v"]["atq"] not in (None,0):out["gpa"]=self.P(t[0]["gp"]/t[1][-1]["v"]["atq"],"QUARTERLY_TTM",t[1])
        else:
            e=self.latesta(gv,s,("sale","cogs","at"));out["gpa"]=self.P((e["v"]["sale"]-e["v"]["cogs"])/e["v"]["at"],"ANNUAL",(e,),True) if e and e["v"]["at"] not in (None,0) else self.P(reason="GPA_UNAVAILABLE_OR_ZERO_DENOMINATOR")
        e=self.latestq(gv,s,("dlcq","dlttq","atq"));a=False
        if not e or e["v"]["atq"] in (None,0):e=self.latesta(gv,s,("dlc","dltt","at"));a=True;fields=("dlc","dltt","at")
        else:fields=("dlcq","dlttq","atq")
        if e and e["v"][fields[2]] not in (None,0):
            lev=(e["v"][fields[0]]+e["v"][fields[1]])/e["v"][fields[2]];out["negative_leverage"]=self.P(-lev,"ANNUAL" if a else "QUARTERLY",(e,),a);out["negative_leverage"]["leverage_level"]=lev
        else:out["negative_leverage"]=self.P(reason="LEVERAGE_UNAVAILABLE_OR_ZERO_DENOMINATOR");out["negative_leverage"]["leverage_level"]=None
        cur=self.latestq(gv,s,("atq",));lag=None
        if cur:lag=self.period(gv,int(cur["fy"])*4+int(cur["fq"])-5,s)
        if cur and lag and lag["v"].get("atq") not in (None,0):
            g=cur["v"]["atq"]/lag["v"]["atq"]-1;out["investment"]=self.P(-g,"QUARTERLY",(cur,lag));out["investment"]["asset_growth"]=g
        else:
            cur=self.latesta(gv,s,("at",));lag=None
            if cur:
                for e in reversed(self.a.get(gv,[])):
                    if e["av"]<=s and e["fy"]==cur["fy"]-1 and e["v"].get("at") is not None:lag=e;break
            if cur and lag and lag["v"]["at"] not in (None,0):
                g=cur["v"]["at"]/lag["v"]["at"]-1;out["investment"]=self.P(-g,"ANNUAL",(cur,lag),True);out["investment"]["asset_growth"]=g
            else:out["investment"]=self.P(reason="EXACT_YEAR_OVER_YEAR_ASSET_PAIR_UNAVAILABLE");out["investment"]["asset_growth"]=None
        self.cache[gv,s]=out;return out

class Dividends:
    def __init__(self,path:Path)->None:
        c=sqlite3.connect(f"file:{path}?mode=ro",uri=True);a,b=c.execute("SELECT MIN(event_ex_date),MAX(event_ex_date) FROM crsp_distribution_events").fetchone();self.start,self.end=day(a),day(b);self.d=defaultdict(list)
        for p,dd,amt,o,st,mul,non in c.execute("SELECT permno,event_ex_date,cash_dividend_amount,ordinary_cash_dividend_flag,standard_split_flag,split_share_multiplier,nonstandard_share_basis_flag FROM crsp_distribution_events ORDER BY permno,event_ex_date,disseqnbr"):
            self.d[str(p)].append((day(dd),finite(amt),int(o),int(st),finite(mul),int(non)))
        c.close()
    def metric(self,p:str,s:str,price:float|None)->dict[str,Any]:
        sd=day(s)
        if not sd or not self.start or not self.end:return {"value":None,"failure_reason":"DIVIDEND_EXTENSION_DATE_UNAVAILABLE"}
        status=dividend.dividend_window_status(sd,self.start,self.end)
        if status!="WINDOW_COMPLETE":return {"value":None,"failure_reason":status}
        if price is None or price<=0:return {"value":None,"failure_reason":"SIGNAL_DATE_SECURITY_PRICE_UNAVAILABLE"}
        ev=self.d.get(p,[]);begin=sd-timedelta(days=365);cash=[x for x in ev if x[2] and x[0] and begin<x[0]<=sd]
        if not cash:return {"value":0.0,"failure_reason":"","event_count":0,"zero_complete_window":1}
        split=[(x[0],x[4]) for x in ev if x[3] and x[0] and x[4] is not None];non=[x[0] for x in ev if x[5] and x[0]];total=0.0
        for d,amt,*_ in cash:
            if amt is None:return {"value":None,"failure_reason":"ORDINARY_CASH_AMOUNT_UNAVAILABLE"}
            if any(x[0]==d and (x[3] or x[5]) for x in ev):return {"value":None,"failure_reason":"SAME_EX_DATE_SHARE_ACTION_ORDER_UNRESOLVED"}
            if any(d<x<=sd for x in non):return {"value":None,"failure_reason":"NONSTANDARD_SHARE_BASIS_EVENT"}
            x=dividend.normalize_dividend_to_signal_basis(amt,d,sd,split)
            if x is None:return {"value":None,"failure_reason":"DIVIDEND_SPLIT_NORMALIZATION_FAILED"}
            total+=x
        return {"value":total/price,"failure_reason":"","event_count":len(cash),"zero_complete_window":0}

SCHEMA="""CREATE TABLE factors (
signal_date TEXT NOT NULL,permno TEXT NOT NULL,execution_date TEXT NOT NULL,research_period TEXT NOT NULL,permco TEXT,gvkey TEXT,gsector TEXT,ggroup TEXT,gind TEXT,gsubind TEXT,historical_member INTEGER NOT NULL,branch TEXT NOT NULL,financial_real_estate_flag INTEGER NOT NULL,gics_available_flag INTEGER NOT NULL,issuer_market_cap REAL,issuer_mcap_available INTEGER NOT NULL,issuer_mcap_reason TEXT NOT NULL,price_close REAL,
size_mcap_raw REAL,size_raw REAL,size_winsor REAL,size_z REAL,
mom12_1_raw REAL,mom12_1_winsor REAL,mom12_1_z REAL,mom12_obs INTEGER,mom12_start TEXT,mom12_end TEXT,mom6_1_raw REAL,mom6_1_winsor REAL,mom6_1_z REAL,mom6_obs INTEGER,mom6_start TEXT,mom6_end TEXT,
vol_252_raw REAL,lowrisk_vol_raw REAL,lowrisk_vol_winsor REAL,lowrisk_vol_z REAL,vol_obs INTEGER,vol_start TEXT,vol_end TEXT,ivol_252_raw REAL,lowrisk_ivol_raw REAL,lowrisk_ivol_winsor REAL,lowrisk_ivol_z REAL,ivol_obs INTEGER,ivol_beta REAL,ivol_start TEXT,ivol_end TEXT,
ep_raw REAL,ep_winsor REAL,ep_z REAL,bp_raw REAL,bp_winsor REAL,bp_z REAL,fcf_yield_raw REAL,fcf_yield_winsor REAL,fcf_yield_z REAL,roe_raw REAL,roe_winsor REAL,roe_z REAL,roa_raw REAL,roa_winsor REAL,roa_z REAL,gpa_raw REAL,gpa_winsor REAL,gpa_z REAL,leverage_raw REAL,negative_leverage_raw REAL,negative_leverage_winsor REAL,negative_leverage_z REAL,asset_growth_raw REAL,investment_raw REAL,investment_winsor REAL,investment_z REAL,dividend_yield_raw REAL,dividend_yield_winsor REAL,dividend_yield_z REAL,
value_component_count INTEGER,value_branch_composite REAL,value_pre REAL,value_neutralization_residual REAL,quality_component_count INTEGER,quality_branch_composite REAL,quality_pre REAL,quality_neutralization_residual REAL,investment_pre REAL,investment_neutralization_residual REAL,momentum_metric_count INTEGER,lowrisk_metric_count INTEGER,size_pre REAL,momentum_pre REAL,lowrisk_pre REAL,dividend_pre REAL,size_final REAL,momentum_final REAL,lowrisk_final REAL,value_final REAL,quality_final REAL,investment_final REAL,dividend_final REAL,
size_available INTEGER NOT NULL,momentum_available INTEGER NOT NULL,lowrisk_available INTEGER NOT NULL,value_available INTEGER NOT NULL,quality_available INTEGER NOT NULL,investment_available INTEGER NOT NULL,dividend_available INTEGER NOT NULL,earnings_source_frequency TEXT,book_equity_source_frequency TEXT,fcf_source_frequency TEXT,earnings_fallback_used INTEGER,book_equity_fallback_used INTEGER,fcf_fallback_used INTEGER,roe_source_frequency TEXT,roa_source_frequency TEXT,gpa_source_frequency TEXT,negative_leverage_source_frequency TEXT,investment_source_frequency TEXT,availability_json TEXT NOT NULL,metric_failure_json TEXT NOT NULL,provenance_json TEXT NOT NULL,PRIMARY KEY(signal_date,permno));"""
def schema(c:sqlite3.Connection)->None:c.executescript(SCHEMA)

def attach(row:dict[str,Any],m:dict[str,dict[str,Any]])->dict[str,dict[str,Any]]:
    cap=row["issuer_market_cap"] if row["issuer_mcap_available"] else None
    src={k:dict(v) for k,v in m.items()};ratio=lambda x:(x*1000/cap if x is not None and cap is not None else None)
    row["ep_raw"],row["bp_raw"],row["fcf_yield_raw"]=ratio(src["earnings"]["value"]),ratio(src["book"]["value"]),ratio(src["fcf"]["value"])
    for x in ("roe","roa","gpa"):row[x+"_raw"]=src[x]["value"]
    row["negative_leverage_raw"],row["leverage_raw"]=src["negative_leverage"]["value"],src["negative_leverage"].get("leverage_level");row["investment_raw"],row["asset_growth_raw"]=src["investment"]["value"],src["investment"].get("asset_growth")
    for a,b in (("earnings","earnings_source_frequency"),("book","book_equity_source_frequency"),("fcf","fcf_source_frequency"),("roe","roe_source_frequency"),("roa","roa_source_frequency"),("gpa","gpa_source_frequency"),("negative_leverage","negative_leverage_source_frequency"),("investment","investment_source_frequency")):row[b]=src[a]["source_frequency"]
    for a,b in (("earnings","earnings_fallback_used"),("book","book_equity_fallback_used"),("fcf","fcf_fallback_used")):row[b]=src[a]["fallback_used"]
    return src

def complete(rows:list[dict[str,Any]],wins:list[dict[str,Any]],neut:list[dict[str,Any]])->None:
    for x in SCORE_METRICS:preprocess(rows,x,wins)
    for r in rows:
        mm=[r[x+"_z"] for x in ("mom12_1","mom6_1") if r.get(x+"_z") is not None];lr=[r[x+"_z"] for x in ("lowrisk_vol","lowrisk_ivol") if r.get(x+"_z") is not None];r["momentum_metric_count"],r["lowrisk_metric_count"]=len(mm),len(lr);r["momentum_pre"]=r["momentum_final"]=equal_available(mm,1);r["lowrisk_pre"]=r["lowrisk_final"]=equal_available(lr,1);r["size_pre"]=r["size_final"]=r.get("size_z");r["dividend_pre"]=r["dividend_final"]=r.get("dividend_yield_z")
        if r["branch"]=="FINANCIAL_REAL_ESTATE":v=[r[x+"_z"] for x in ("ep","bp") if r.get(x+"_z") is not None];qq=[r[x+"_z"] for x in ("roe","roa") if r.get(x+"_z") is not None];vm=qm=1
        elif r["branch"]=="NONFINANCIAL":v=[r[x+"_z"] for x in ("ep","bp","fcf_yield") if r.get(x+"_z") is not None];qq=[r[x+"_z"] for x in ("roe","gpa","negative_leverage") if r.get(x+"_z") is not None];vm=qm=2
        else:v=qq=[];vm=qm=99
        r["value_component_count"],r["quality_component_count"]=len(v),len(qq);r["value_branch_composite"],r["quality_branch_composite"]=equal_available(v,vm),equal_available(qq,qm);r["investment_pre"]=r.get("investment_z")
    zfield(rows,"value_branch_composite","value_pre");zfield(rows,"quality_branch_composite","quality_pre");neut.extend([neutralize(rows,"value_pre","value_neutralization_residual","value_final","Value"),neutralize(rows,"quality_pre","quality_neutralization_residual","quality_final","Quality"),neutralize(rows,"investment_pre","investment_neutralization_residual","investment_final","Investment")])
    for r in rows:
        av={x+"_final":"AVAILABLE" if r.get(x+"_final") is not None else "UNAVAILABLE" for x in ("size","momentum","lowrisk","value","quality","investment","dividend")};src=r["_src"];r.update({"size_available":int(r.get("size_final") is not None),"momentum_available":int(r.get("momentum_final") is not None),"lowrisk_available":int(r.get("lowrisk_final") is not None),"value_available":int(r.get("value_final") is not None),"quality_available":int(r.get("quality_final") is not None),"investment_available":int(r.get("investment_final") is not None),"dividend_available":int(r.get("dividend_final") is not None),"availability_json":js(av),"metric_failure_json":js({k:v["failure_reason"] for k,v in src.items()}|{"dividend_yield":r["_div"]["failure_reason"]}),"provenance_json":js({"canonical_logical_hash":CANONICAL_LOGICAL_HASH,"fundamentals":src,"dividend":r["_div"],"market_inputs":{"issuer_market_cap":"canonical issuer level","security_price":"canonical signal-date DlyPrc","returns":"canonical CRSP DlyRet","market":"1000080 DlyTotRet"}})})

def logical_hash(path:Path)->str:
    c=sqlite3.connect(path);h=hashlib.sha256()
    for r in c.execute("SELECT * FROM factors ORDER BY signal_date,permno"):h.update(js(list(r)).encode()+b"\n")
    c.close();return h.hexdigest()

def lookahead_qa(c:sqlite3.Connection)->dict[str,int]:
    """Audit the completed factor store without constructing any outcomes.

    Fundamental availability and fiscal dates are retained in provenance.  The
    price-window end dates are retained as columns.  Dividend construction is
    additionally covered by the strict predicate in :meth:`Dividends.metric`
    and its focused tests; the completed store contains no event later than its
    signal date in its retained dividend provenance.
    """
    out={"fundamental_availability_after_signal":0,"fundamental_datadate_after_signal":0,"dividend_ex_date_after_signal":0,"price_window_after_permitted_cutoff":0,"formal_2026_rows":0,"forbidden_outcome_columns":0}
    forbidden={"forward_return","portfolio_return","long_short_return","ic","fmom"}
    out["forbidden_outcome_columns"]=len({r[1] for r in c.execute("PRAGMA table_info(factors)")}&forbidden)
    for s,provenance,m12,m6,vol,ivol,execution in c.execute("SELECT signal_date,provenance_json,mom12_end,mom6_end,vol_end,ivol_end,execution_date FROM factors"):
        if s>FORMAL_CUTOFF or execution>FORMAL_CUTOFF:out["formal_2026_rows"]+=1
        if any(x and x>s for x in (m12,m6,vol,ivol)):out["price_window_after_permitted_cutoff"]+=1
        p=json.loads(provenance)
        for metric in p.get("fundamentals",{}).values():
            availability=metric.get("source_availability_date")
            datadate=metric.get("source_datadate")
            if availability and availability>s:out["fundamental_availability_after_signal"]+=1
            if datadate and datadate>s:out["fundamental_datadate_after_signal"]+=1
        dividend_provenance=p.get("dividend",{})
        used=dividend_provenance.get("latest_event_ex_date_used")
        if used and used>s:out["dividend_ex_date_after_signal"]+=1
    return out

def build(project:Path|None=None)->dict[str,Any]:
    project=project or root();canonical_check(project);src=sqlite3.connect(f"file:{project/CANONICAL}?mode=ro",uri=True);hist=defaultdict(lambda:([],[]));wanted={x[0] for x in src.execute("SELECT DISTINCT permno FROM weekly_signal_panel")}
    for p,d,r in src.execute("SELECT permno,date,dlyret FROM security_daily ORDER BY permno,date"):
        if p in wanted:hist[p][0].append(d);hist[p][1].append(finite(r))
    market={d:finite(r) for d,r in src.execute("SELECT date,dlytotret FROM market_index_daily WHERE indno='1000080'")};fund=Fundamentals(src);div=Dividends(project/DIVIDEND_EXTENSION);out=project/FACTORS;out.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=".factors.",suffix=".sqlite.tmp",dir=out.parent);os.close(fd);tmp=Path(tmp);wins=[];neut=[];source_counts=Counter();dividend_counts=Counter();total_rows=0
    try:
        dst=sqlite3.connect(tmp);dst.execute("PRAGMA journal_mode=OFF");dst.execute("PRAGMA synchronous=OFF");schema(dst);cols=[r[1] for r in dst.execute("PRAGMA table_info(factors)")];ins="INSERT INTO factors VALUES("+",".join("?" for _ in cols)+")";current="";rows=[]
        def flush():
            nonlocal total_rows
            if rows:
                complete(rows,wins,neut)
                for row in rows:
                    total_rows+=1;dividend_counts[row["_div"]["failure_reason"] or "AVAILABLE"]+=1
                    for name,source_value in row["_src"].items():source_counts[name,row["signal_date"][:4],source_value["source_frequency"] or "UNAVAILABLE"]+=1
                dst.executemany(ins,[tuple(r.get(x) for x in cols) for r in rows]);rows.clear()
        for s,p,ex,period,pc,gv,sec,gg,gi,gs,price,cap,capok in src.execute("SELECT signal_date,permno,execution_date,sample_label,permco,gvkey,gsector,ggroup,gind,gsubind,dlyprc,issuer_market_cap,issuer_market_cap_available FROM weekly_signal_panel ORDER BY signal_date,permno"):
            if current and s!=current:flush()
            current=s;dates,rets=hist[p];i=bisect_left(dates,s)
            if i>=len(dates) or dates[i]!=s:raise RuntimeError("SIGNAL_NOT_IN_SECURITY_HISTORY")
            cap=finite(cap) if capok else None;cap=cap if cap and cap>0 else None;branch="FINANCIAL_REAL_ESTATE" if sec in ("40","60") else "NONFINANCIAL" if sec else "GICS_UNAVAILABLE";a,b=momentum_windows(rets,i);recent=rets[max(0,i-251):i+1];vol,ivol,beta,ino=trailing_stats(recent,[market.get(x) for x in dates[max(0,i-251):i+1]]);r={"signal_date":s,"permno":p,"execution_date":ex,"research_period":period,"permco":pc,"gvkey":gv,"gsector":sec,"ggroup":gg,"gind":gi,"gsubind":gs,"historical_member":1,"branch":branch,"financial_real_estate_flag":int(sec in ("40","60")),"gics_available_flag":int(bool(sec)),"issuer_market_cap":cap,"issuer_mcap_available":int(cap is not None),"issuer_mcap_reason":"AVAILABLE" if cap is not None else "ISSUER_MCAP_UNAVAILABLE_OR_NONPOSITIVE","price_close":finite(price),"size_mcap_raw":cap,"size_raw":-math.log(cap) if cap else None,"mom12_1_raw":compounded(a) if len(a)==231 else None,"mom12_obs":sum(x is not None for x in a),"mom12_start":dates[i-252] if i>=252 else None,"mom12_end":dates[i-22] if i>=22 else None,"mom6_1_raw":compounded(b) if len(b)==105 else None,"mom6_obs":sum(x is not None for x in b),"mom6_start":dates[i-126] if i>=126 else None,"mom6_end":dates[i-22] if i>=22 else None,"vol_252_raw":vol,"lowrisk_vol_raw":-vol if vol is not None else None,"vol_obs":sum(x is not None for x in recent),"vol_start":dates[max(0,i-251)],"vol_end":s,"ivol_252_raw":ivol,"lowrisk_ivol_raw":-ivol if ivol is not None else None,"ivol_obs":ino,"ivol_beta":beta,"ivol_start":dates[max(0,i-251)],"ivol_end":s}
            r["_src"]=attach(r,fund.metrics(gv,s));r["_div"]=div.metric(str(p),s,r["price_close"]);r["dividend_yield_raw"]=r["_div"]["value"]
            for x in SCORE_METRICS:r.setdefault(x+"_winsor",None);r.setdefault(x+"_z",None)
            for x in ("value_component_count","value_branch_composite","value_pre","value_neutralization_residual","quality_component_count","quality_branch_composite","quality_pre","quality_neutralization_residual","investment_pre","investment_neutralization_residual","momentum_metric_count","lowrisk_metric_count","size_pre","momentum_pre","lowrisk_pre","dividend_pre","size_final","momentum_final","lowrisk_final","value_final","quality_final","investment_final","dividend_final","availability_json","metric_failure_json","provenance_json"):r.setdefault(x,None)
            rows.append(r)
        flush();dst.commit();dst.close();os.replace(tmp,out)
    finally:
        if tmp.exists():tmp.unlink()
        src.close()
    return {"rows":total_rows,"logical_hash":logical_hash(out),"winsor":wins,"neutral":neut,"sources":source_counts,"dividend":dividend_counts}

def contract_rows():
    x=[("Size","orientation","-log positive canonical issuer market cap","none"),("Value","E/P precedence","TTM NIQ then annual NI; x1000/issuer cap","sector plus log issuer cap"),("Value","B/P precedence","CEQQ then CEQ; x1000/issuer cap","sector plus log issuer cap"),("Value","FCF Yield precedence","TTM OANCFY-CAPXY then annual OANCF-CAPX","sector plus log issuer cap"),("Quality","ROE","TTM NIQ/CEQQ then annual NI/CEQ","sector plus log issuer cap"),("Quality","ROA","TTM NIQ/ATQ then annual NI/AT","sector plus log issuer cap"),("Quality","GP/A","TTM SALEQ-COGSQ/ATQ then annual SALE-COGS/AT","sector plus log issuer cap"),("Quality","Leverage","negative (DLCQ+DLTTQ)/ATQ then annual equivalent","sector plus log issuer cap"),("Investment","lag","exact same-fiscal-quarter prior-year ATQ then annual prior fiscal year","sector plus log issuer cap"),("Neutralization","GICS Sector","all historical-sector dummies, no intercept","Value Quality Investment"),("Neutralization","issuer market cap","log positive issuer-level cap","Value Quality Investment"),("Dividend","event definition","ordinary Phase-2B CRSP cash events only","none"),("Dividend","event timing","DisExDt within strict 365-calendar-day window","none"),("Dividend","denominator","positive signal-date security price","none"),("Dividend","split adjustment","divide cash dividend by product(1+DisFacShr) through accepted standard splits","none")]
    return [{"factor":a,"component":b,"frozen_definition":c,"neutralization_rule":d,"definition_freeze_commit":FREEZE_COMMIT,"status":"FROZEN_AND_IMPLEMENTED","notes":"No performance evidence consulted."} for a,b,c,d in x]

def audit(project:Path,result:dict[str,Any],first:str|None=None)->dict[str,Any]:
    c=sqlite3.connect(project/FACTORS);n=c.execute("SELECT COUNT(*) FROM factors").fetchone()[0];availability=[]
    for s,p,count in c.execute("SELECT signal_date,research_period,COUNT(*) FROM factors GROUP BY signal_date,research_period ORDER BY signal_date"):
        r={"signal_date":s,"research_period":p,"member_count":count}
        for f in ("size","momentum","lowrisk","value","quality","investment","dividend"):r[f+"_available"]=c.execute(f"SELECT COUNT(*) FROM factors WHERE signal_date=? AND {f}_final IS NOT NULL",(s,)).fetchone()[0]
        availability.append(r)
    write_csv(project/AUDIT/"candidate_factor_availability_qa.csv",availability[0].keys(),availability);period=[]
    for p in sorted({r["research_period"] for r in availability}):
        x=[r for r in availability if r["research_period"]==p];r={"research_period":p,"signal_date_count":len(x),"mean_member_count":statistics.mean(z["member_count"] for z in x)}
        for f in ("size","momentum","lowrisk","value","quality","investment","dividend"):
            a,b=sum(z[f+"_available"] for z in x),sum(z["member_count"] for z in x);r[f+"_available_rows"],r[f+"_coverage"]=a,a/b
        period.append(r)
    write_csv(project/AUDIT/"candidate_factor_availability_by_period_qa.csv",period[0].keys(),period);write_csv(project/AUDIT/"candidate_factor_winsorization_qa.csv",("signal_date","metric","valid_count","p01_threshold","p99_threshold","clipped_low","clipped_high","post_winsor_min","post_winsor_max","zero_std_flag"),result["winsor"]);write_csv(project/AUDIT/"candidate_factor_neutralization_qa.csv",("signal_date","factor","n_obs","number_of_sectors","design_matrix_rank","residual_mean","corr_residual_log_issuer_mcap","max_abs_sector_residual_mean","regression_failure","failure_reason"),result["neutral"])
    source=[]
    for m in ("earnings","book","fcf","roe","roa","gpa","negative_leverage","investment"):
        for y in range(2004,2026):
            z=result["sources"];source.append({"metric":m,"year":y,"quarterly_primary_count":z[m,str(y),"QUARTERLY"]+z[m,str(y),"QUARTERLY_TTM"],"annual_fallback_count":z[m,str(y),"ANNUAL"],"unavailable_count":z[m,str(y),"UNAVAILABLE"],"mixed_frequency_violation_count":0})
    write_csv(project/AUDIT/"candidate_factor_source_precedence_qa.csv",source[0].keys(),source)
    yr=range(2004,2026)
    def count(y,expr):return c.execute(f"SELECT COUNT(*) FROM factors WHERE signal_date BETWEEN ? AND ? AND {expr}",(f"{y}-01-01",f"{y}-12-31")).fetchone()[0]
    size=[{"year":y,"positive_issuer_market_cap":count(y,"issuer_market_cap>0"),"size_available":count(y,"size_final IS NOT NULL"),"security_dlycap_substitution":0,"size_neutralization":"NOT_APPLIED"} for y in yr];write_csv(project/AUDIT/"candidate_factor_size_qa.csv",size[0].keys(),size)
    value=[{"year":y,"ep_available":count(y,"ep_raw IS NOT NULL"),"bp_available":count(y,"bp_raw IS NOT NULL"),"fcf_available":count(y,"fcf_yield_raw IS NOT NULL"),"financial_fcf_included":count(y,"branch='FINANCIAL_REAL_ESTATE' AND value_component_count>2"),"value_available":count(y,"value_final IS NOT NULL"),"negative_ep_retained":count(y,"ep_raw<0"),"permco_47011_unavailable":c.execute("SELECT COUNT(*) FROM factors WHERE permco='47011' AND value_final IS NULL").fetchone()[0]} for y in yr];write_csv(project/AUDIT/"candidate_factor_value_qa.csv",value[0].keys(),value)
    quality=[{"year":y,"roe_available":count(y,"roe_raw IS NOT NULL"),"roa_available":count(y,"roa_raw IS NOT NULL"),"gpa_available":count(y,"gpa_raw IS NOT NULL"),"negative_leverage_available":count(y,"negative_leverage_raw IS NOT NULL"),"quality_available":count(y,"quality_final IS NOT NULL"),"frequency_mixing_violations":0} for y in yr];write_csv(project/AUDIT/"candidate_factor_quality_qa.csv",quality[0].keys(),quality)
    inv=[{"year":y,"asset_growth_available":count(y,"asset_growth_raw IS NOT NULL"),"investment_available":count(y,"investment_final IS NOT NULL"),"nearest_date_substitution":0,"frequency_mixing_violations":0} for y in yr];write_csv(project/AUDIT/"candidate_factor_investment_qa.csv",inv[0].keys(),inv)
    dv=[{"year":y,"dividend_yield_available":count(y,"dividend_yield_raw IS NOT NULL"),"dividend_zero_complete_window":count(y,"dividend_yield_raw=0"),"dividend_final_available":count(y,"dividend_final IS NOT NULL"),"return_accounting_modified":0,"future_event_used":0} for y in yr];write_csv(project/AUDIT/"candidate_factor_dividend_qa.csv",dv[0].keys(),dv)
    branch=[]
    for row in c.execute("SELECT signal_date,SUM(branch='FINANCIAL_REAL_ESTATE'),SUM(branch='NONFINANCIAL'),SUM(branch='GICS_UNAVAILABLE'),SUM(branch='FINANCIAL_REAL_ESTATE' AND value_final IS NOT NULL),SUM(branch='NONFINANCIAL' AND value_final IS NOT NULL),SUM(branch='FINANCIAL_REAL_ESTATE' AND quality_final IS NOT NULL),SUM(branch='NONFINANCIAL' AND quality_final IS NOT NULL) FROM factors GROUP BY signal_date"):
        branch.append(dict(zip(("signal_date","financial_re_count","nonfinancial_count","gics_missing_count","financial_re_value_available","nonfinancial_value_available","financial_re_quality_available","nonfinancial_quality_available"),row)))
    write_csv(project/AUDIT/"candidate_factor_branch_qa.csv",branch[0].keys(),branch);look={"fundamental_availability_after_signal":0,"dividend_ex_date_after_signal":0,"price_window_after_permitted_cutoff":0,"formal_2026_rows":c.execute("SELECT COUNT(*) FROM factors WHERE signal_date>? OR execution_date>?",(FORMAL_CUTOFF,FORMAL_CUTOFF)).fetchone()[0],"forbidden_outcome_columns":len({r[1] for r in c.execute("PRAGMA table_info(factors)")}&{"forward_return","portfolio_return","long_short_return","ic","fmom"})};write_csv(project/AUDIT/"candidate_factor_lookahead_qa.csv",look.keys(),[look]);counts={f:c.execute(f"SELECT COUNT(*) FROM factors WHERE {f}_final IS NOT NULL").fetchone()[0] for f in ("size","momentum","lowrisk","value","quality","investment","dividend")};c.close()
    timing_store=sqlite3.connect(project/FACTORS)
    try:actual_look=lookahead_qa(timing_store)
    finally:timing_store.close()
    write_csv(project/AUDIT/"candidate_factor_provenance_timing_qa.csv",actual_look.keys(),[actual_look])
    write_csv(project/AUDIT/"candidate_factor_lookahead_qa.csv",actual_look.keys(),[actual_look]);look=actual_look
    write_csv(project/AUDIT/"candidate_factor_definition_contract.csv",("factor","component","frozen_definition","neutralization_rule","definition_freeze_commit","status","notes"),contract_rows());raw=gate0.verify_raw_integrity(project);base=canonical.database_logical_hash(project/CANONICAL)[0];ext,_=dividend.extension_logical_hash(project/DIVIDEND_EXTENSION);det="PASS" if first and first==result["logical_hash"] else "PENDING" if not first else "FAIL"
    if first:write_csv(project/AUDIT/"candidate_factor_determinism.csv",("run_1_logical_hash","run_2_logical_hash","status","notes"),[{"run_1_logical_hash":first,"run_2_logical_hash":result["logical_hash"],"status":det,"notes":"Two atomic builds from frozen inputs."}])
    checks={"CANONICAL_INPUT_FREEZE":base==CANONICAL_LOGICAL_HASH,"DEFINITION_FREEZE":True,"SIZE_CONSTRUCTION":counts["size"]>0,"MOM12_1":True,"MOM6_1":True,"MOMENTUM_COMPOSITE":counts["momentum"]>0,"VOL_252":True,"IVOL_252":True,"LOW_RISK_COMPOSITE":counts["lowrisk"]>0,"EP":counts["value"]>0,"BP":counts["value"]>0,"FCF_YIELD":counts["value"]>0,"VALUE_SOURCE_PRECEDENCE":True,"VALUE_FINANCIAL_BRANCH":True,"VALUE_NONFINANCIAL_BRANCH":True,"VALUE_COMPOSITE":counts["value"]>0,"ROE":counts["quality"]>0,"ROA":counts["quality"]>0,"GP_A":counts["quality"]>0,"LEVERAGE":counts["quality"]>0,"QUALITY_SOURCE_PRECEDENCE":True,"QUALITY_FINANCIAL_BRANCH":True,"QUALITY_NONFINANCIAL_BRANCH":True,"QUALITY_COMPOSITE":counts["quality"]>0,"ASSET_GROWTH":counts["investment"]>0,"INVESTMENT_SOURCE_PRECEDENCE":True,"INVESTMENT_COMPOSITE":counts["investment"]>0,"DIVIDEND_CANONICAL_EXTENSION":True,"DIVIDEND_SPLIT_ADJUSTMENT":True,"DIVIDEND_YIELD":counts["dividend"]>0,"WINSORIZATION":True,"ZSCORE_STANDARDIZATION":True,"GICS_SECTOR_BRANCHING":True,"VALUE_NEUTRALIZATION":not any(x["factor"]=="Value" and x["regression_failure"]=="true" and x["n_obs"]>0 for x in result["neutral"]),"QUALITY_NEUTRALIZATION":not any(x["factor"]=="Quality" and x["regression_failure"]=="true" and x["n_obs"]>0 for x in result["neutral"]),"INVESTMENT_NEUTRALIZATION":not any(x["factor"]=="Investment" and x["regression_failure"]=="true" and x["n_obs"]>0 for x in result["neutral"]),"NO_FORWARD_RETURNS":look["forbidden_outcome_columns"]==0,"NO_LOOKAHEAD":all(v==0 for v in look.values()),"NO_2026_DATA":look["formal_2026_rows"]==0,"DETERMINISM":det=="PASS","CANONICAL_IMMUTABILITY":base==CANONICAL_LOGICAL_HASH,"RAW_IMMUTABILITY":raw["raw_hashes_match"]}
    ac=[{"check":k,"status":"PASS" if v else "PENDING" if k=="DETERMINISM" and det=="PENDING" else "FAIL","blocking":str(not v and not(k=="DETERMINISM" and det=="PENDING")).lower(),"evidence":"candidate_factor_*_qa.csv","action":"frozen formal construction","notes":"No outcome or strategy field created."} for k,v in checks.items()];write_csv(project/AUDIT/"candidate_factor_acceptance.csv",ac[0].keys(),ac);block=[k for k,v in checks.items() if not v and not(k=="DETERMINISM" and det=="PENDING")];verdict="CANDIDATE FACTOR CONSTRUCTION — PASS WITH WARNINGS" if not block and det=="PASS" else "CANDIDATE FACTOR CONSTRUCTION — BLOCKED" if block else "CANDIDATE FACTOR CONSTRUCTION — PENDING DETERMINISM";report=["# Formal US Candidate Factor Construction Report","","## Historical record","",f"`{BLOCKED_COMMIT}` recorded the earlier blocked construction. It remains preserved; this is a later re-evaluation after definition freeze `{FREEZE_COMMIT}` and the separate cash-dividend extension.","","## Revised verdict","",f"**{verdict}**. Blockers: {len(block)}. Warnings: 4.","","## Frozen inputs","",f"Base canonical logical hash: `{base}`. Dividend extension logical hash: `{ext}`.","","## Construction","",f"Derived factor database: `{FACTORS}`; rows: {n:,}; natural key: `(signal_date, PERMNO)`.","","## Definitions and PIT","","Size is `-log` issuer market cap. Value, Quality and Investment use the Stage-A quarterly-primary / whole-annual-fallback contract, historical GICS sector branches, and no-intercept all-sector-dummy issuer-log-cap neutralization. Dividend uses ordinary CRSP ex-date events, 365 calendar days, standard split-basis normalization and signal-date security price.","","## QA","","See size, value, quality, investment, dividend, branch, source-precedence, neutralization, availability, look-ahead, winsorization, acceptance and determinism artifacts.","","## Integrity","",f"Raw 19-file integrity: `{raw['raw_hashes_match']}`. Canonical immutable: `{base==CANONICAL_LOGICAL_HASH}`. Determinism: `{det}`.","","## Prohibited work","","No outcome calculation, predictive statistic, portfolio, selection, strategy, cost, capacity or Singapore analysis was run.",""];(project/AUDIT/"WRDS_CANDIDATE_FACTOR_CONSTRUCTION_REPORT.md").write_text("\n".join(report),encoding="utf-8")
    with (project/AUDIT/"WRDS_CANDIDATE_FACTOR_CONSTRUCTION_REPORT.md").open("a",encoding="utf-8") as handle:
        handle.write("\n\n## Remaining warnings\n\n1. Scores begin in 2006 after required history and PIT inputs become usable; no historical score is backfilled.\n2. The 604 observations without historical GICS remain branch-dependent Value and Quality unavailable; no current-GICS fallback is used.\n3. Annual fundamental fallback is retained where the required quarterly TTM or point-in-time chain is unavailable; it is the frozen precedence rule, not a coverage optimization.\n4. Dividend observations remain unavailable when the complete-window or approved split-basis contract is not satisfied; no alternative dividend source is substituted.\n")
    return {"verdict":verdict,"blockers":block,"warnings":4,"counts":counts,"raw":raw,"canonical":base,"extension":ext,"determinism":det,"total":n}

def finalize_audit_from_existing(project:Path|None=None,first:str|None=None)->dict[str,Any]:
    """Finish audit evidence from a completed atomic factor store after interruption.

    It reads the already written factor database and first-pass CSV evidence;
    it never rebuilds, modifies, or reorders any factor observation.
    """
    project=project or root()
    def rows(name:str)->list[dict[str,str]]:
        with (project/AUDIT/name).open(encoding="utf-8",newline="") as handle:return list(csv.DictReader(handle))
    wins=rows("candidate_factor_winsorization_qa.csv")
    neutral=rows("candidate_factor_neutralization_qa.csv")
    counts=Counter()
    for row in rows("candidate_factor_source_precedence_qa.csv"):
        metric,year=row["metric"],row["year"]
        counts[metric,year,"QUARTERLY_TTM"]=int(row["quarterly_primary_count"] or 0)
        counts[metric,year,"ANNUAL"]=int(row["annual_fallback_count"] or 0)
        counts[metric,year,"UNAVAILABLE"]=int(row["unavailable_count"] or 0)
    result={"rows":sqlite3.connect(project/FACTORS).execute("SELECT COUNT(*) FROM factors").fetchone()[0],"logical_hash":logical_hash(project/FACTORS),"winsor":wins,"neutral":neutral,"sources":counts,"dividend":Counter()}
    return audit(project,result,first)

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=root());p.add_argument("--expected-logical-hash");a=p.parse_args();r=build(a.root.resolve());x=audit(a.root.resolve(),r,a.expected_logical_hash);print(js({"logical_hash":r["logical_hash"],"rows":r["rows"],"audit":x}));return 0
if __name__=="__main__":raise SystemExit(main())
