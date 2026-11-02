"""Full backtest report. Usage: python report_bt.py  -> writes backtest_results.json + prints tables."""
from analysis import *
import json
G=pd.read_parquet(D+'games.parquet')[['game_id','home_conference_id','away_conference_id']]
POWER={2,4,7,8,21,23}
R={}
def acc_table():
    L=pd.read_parquet(D+'lines.parquet')[['game_id','spread','total']].rename(columns={'total':'line_total'})
    out={}
    for tag in ['v1','noprior','lastyr','points']:
        P=pd.read_parquet(D+f'preds_{tag}.parquet').merge(L,on='game_id')
        e=P.margin-P.pred_margin; et=P.total-P.pred_total
        out[tag]=dict(mae=e.abs().groupby(P.season).mean(),tot_mae=et.abs().groupby(P.season).mean(),
                      early_mae=e[P.hg+P.ag<10].abs().groupby(P.season[P.hg+P.ag<10]).mean())
    P=pd.read_parquet(D+'preds_v1.parquet').merge(L,on='game_id')
    from backtest import elo_run
    E=elo_run(range(2013,2027)); P=P.merge(E,on=['game_id','season'],how='left')
    ee=P.margin-P.elo_margin
    mk=P.margin+P.spread; mkt=P.total-P.line_total
    t=pd.DataFrame({f'{k}_mae':v['mae'] for k,v in out.items()})
    t['elo_mae']=ee.abs().groupby(P.season).mean(); t['market_mae']=mk.abs().groupby(P.season).mean()
    t['v1_tot_mae']=out['v1']['tot_mae']; t['points_tot_mae']=out['points']['tot_mae']; t['market_tot_mae']=mkt.abs().groupby(P.season).mean()
    t['v1_early_mae']=out['v1']['early_mae']; t['noprior_early_mae']=out['noprior']['early_mae']
    t['n']=P.groupby('season').size()
    return t.round(3)
if __name__=='__main__':
    A=acc_table(); print(A.to_string()); R['accuracy_by_season']=A.reset_index().to_dict('records')
    df=prep(load('v1')).merge(G,on='game_id',how='left')
    tr=df[df.season.isin(TRAIN)]; c_tr=calibrate(tr); print('calibration TRAIN',c_tr); R['calibration_train']=c_tr
    # does the shrink depend on how far into the season we are?
    for lab,m in (('early (<10 combined games)',tr.hg+tr.ag<10),('rest',tr.hg+tr.ag>=10)):
        print(lab,calibrate(tr[m]))
    va=df[df.season.isin(VAL)]
    grid=[0.0,0.01,0.02,0.03,0.04,0.05,0.07,0.10]
    rows=[]
    for tau in grid:
        B=bets(va,c_tr,tau,tau,tau)
        for mk in ['SPREAD','TOTAL','ML']:
            b=B[B.market==mk]; s=summ(b).iloc[0].to_dict(); rows.append(dict(tau=tau,market=mk,**s))
    V=pd.DataFrame(rows); print('VALIDATION'); print(V.to_string()); R['validation_grid']=V.to_dict('records')
    # pre-registered selection: per market, the tau with the most validation units among taus with >=150 bets;
    # the market is ENABLED only if that validation ROI is > 0.
    rule={}
    for mk in ['SPREAD','TOTAL','ML']:
        v=V[(V.market==mk)&(V.bets>=150)]
        best=v.sort_values('units',ascending=False).iloc[0] if len(v) else None
        rule[mk]=dict(tau=float(best.tau) if best is not None else 0.05, enabled=bool(best is not None and best.roi>0),
                      val_roi=float(best.roi) if best is not None else None, val_bets=int(best.bets) if best is not None else 0)
    print('RULES',rule); R['rules']=rule
    # TEST: recalibrate on train+val, apply frozen rules to untouched 2023 + 2026
    c_tv=calibrate(df[df.season.isin(TRAIN+VAL)]); print('calibration TRAIN+VAL',c_tv); R['calibration_final']=c_tv
    te=df[df.season.isin(TEST)]
    BT=bets(te,c_tv,rule['SPREAD']['tau'],rule['TOTAL']['tau'],rule['ML']['tau'])
    BT=BT.merge(G,on='game_id',how='left')
    BT['enabled']=BT.market.map({k:v['enabled'] for k,v in rule.items()})
    print('TEST all markets at frozen thresholds'); s=summ(BT,'market'); print(s.to_string()); R['test_by_market']=s.reset_index().to_dict('records')
    print(summ(BT,['season','market']).to_string()); R['test_by_season_market']=summ(BT,['season','market']).reset_index().to_dict('records')
    BT['ev_bucket']=pd.cut(BT.ev,[0,0.03,0.05,0.08,0.12,1],right=False)
    print(summ(BT,['market','ev_bucket']).to_string()); R['test_by_ev_bucket']=summ(BT,['market','ev_bucket']).reset_index().astype({'ev_bucket':str}).to_dict('records')
    print(summ(BT,['market','month']).to_string()); R['test_by_month']=summ(BT,['market','month']).reset_index().to_dict('records')
    sp=BT[BT.market!='TOTAL'].copy(); sp['fav_dog']=np.where(sp.fav,'favorite','underdog')
    print(summ(sp,['market','fav_dog']).to_string()); R['test_fav_dog']=summ(sp,['market','fav_dog']).reset_index().to_dict('records')
    BT['conf_tier']=np.where(BT.home_conference_id.isin(POWER)|BT.away_conference_id.isin(POWER),'power-conf team','mid/low-major only')
    print(summ(BT,['market','conf_tier']).to_string()); R['test_conf']=summ(BT,['market','conf_tier']).reset_index().to_dict('records')
    dd={mk:max_dd(BT[BT.market==mk].sort_values('day').units.values) for mk in ['SPREAD','TOTAL','ML']}
    print('max drawdown (units, flat 1u)',dd); R['test_max_drawdown_units']=dd
    # calibration of the probabilities on ALL test games (model's preferred side), not just bets
    ALL=bets(te,c_tv,-1,-1,-1)
    ALL=ALL[ALL.result!='P']; ALL['pbin']=pd.cut(ALL.p,[0,.45,.5,.52,.54,.56,.58,.6,.65,1])
    cal=ALL.groupby(['market','pbin'],observed=True).apply(lambda g: pd.Series(dict(n=len(g),pred=g.p.mean(),actual=(g.result=='W').mean())),include_groups=False).round(4)
    print(cal.to_string()); R['test_calibration']=cal.reset_index().astype({'pbin':str}).to_dict('records')
    # raw ATS of the model's side by point-edge bucket on TEST (no EV filter)
    sp=ALL[ALL.market=='SPREAD'].copy(); sp['edge_bucket']=pd.cut(sp.edge_pts.abs(),[0,1,2,3,4,6,8,50])
    ats=sp.groupby('edge_bucket',observed=True).apply(lambda g: pd.Series(dict(n=len(g),cover=(g.result=='W').mean())),include_groups=False).round(4)
    print(ats.to_string()); R['test_ats_by_edge']=ats.reset_index().astype({'edge_bucket':str}).to_dict('records')
    tt=ALL[ALL.market=='TOTAL'].copy(); tt['edge_bucket']=pd.cut(tt.edge_pts.abs(),[0,2,4,6,8,10,60])
    ou=tt.groupby('edge_bucket',observed=True).apply(lambda g: pd.Series(dict(n=len(g),hit=(g.result=='W').mean())),include_groups=False).round(4)
    print(ou.to_string()); R['test_ou_by_edge']=ou.reset_index().astype({'edge_bucket':str}).to_dict('records')
    BT.to_csv(D+'test_bets.csv',index=False)
    json.dump(R,open(D+'backtest_results.json','w'),indent=1,default=str)
