/* Pure, testable calculations shared by dashboard.js and Node's test runner. */
(function(root,factory){const value=factory(); if(typeof module==='object'&&module.exports)module.exports=value;else root.MarketCore=value})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  function filterMarkets(rows,query='',category='all',sort='default'){
    const q=query.trim().toLocaleLowerCase();
    const result=rows.filter(r=>(category==='all'||r.cat===category)&&(!q||[r.name,r.key,r.code].join(' ').toLocaleLowerCase().includes(q)));
    if(sort==='name')return result.sort((a,b)=>a.name.localeCompare(b.name,'ja'));
    if(['gain','loss','z'].includes(sort))result.sort((a,b)=>{
      if(sort!=='z'&&((a.unit==='rate')!==(b.unit==='rate')))return a.unit==='rate'?1:-1;
      const av=sort==='z'?Math.abs(a.z1d??NaN):a.chg1d,bv=sort==='z'?Math.abs(b.z1d??NaN):b.chg1d;
      if(!finite(av))return finite(bv)?1:0;if(!finite(bv))return -1;
      return sort==='loss'?av-bv:bv-av;
    });return result;
  }
  function compare(a,b,days){
    const dates=Object.keys(a||{}).filter(d=>finite(a[d])&&a[d]>0&&finite(b?.[d])&&b[d]>0).sort();
    if(dates.length<2)return {dates:[],a:[],b:[]};
    const end=new Date(dates.at(-1)+'T00:00:00Z');end.setUTCDate(end.getUTCDate()-days);
    const subset=dates.filter(d=>d>=end.toISOString().slice(0,10));
    if(subset.length<2)return {dates:[],a:[],b:[]};
    return {dates:subset,a:subset.map(d=>a[d]/a[subset[0]]*100),b:subset.map(d=>b[d]/b[subset[0]]*100)};
  }
  function hit(alert,value){return finite(value)&&finite(alert.target)&&(alert.direction==='above'?value>=alert.target:value<=alert.target)}
  function fresh(data,now=Date.now()){const age=now-Date.parse(data?.generated_at);return finite(age)&&age>=-300000&&age<=150*60000&&data?.mode!=='mock'&&data?.mode!=='demo'&&data?.mode!=='snapshot'}
  function backup(value){
    if(!value||value.version!==2||!Array.isArray(value.favorites)||!Array.isArray(value.alerts)||!Array.isArray(value.notes))throw Error('潮目のバックアップ形式ではありません');
    if(value.favorites.length>150||value.alerts.length>100||value.notes.length>500)throw Error('保存件数の上限を超えています');
    const favorites=[...new Set(value.favorites.filter(k=>typeof k==='string'&&/^[A-Z0-9_]{1,20}$/.test(k)))];
    const alerts=value.alerts.map(a=>{
      if(!a||typeof a.id!=='string'||typeof a.key!=='string'||!finite(a.target)||a.target<=0||!['above','below'].includes(a.direction))throw Error('アラートの形式が不正です');
      return {id:a.id.slice(0,80),key:a.key.slice(0,20),direction:a.direction,target:a.target,triggered:!!a.triggered,triggeredAt:typeof a.triggeredAt==='string'?a.triggeredAt.slice(0,40):null};
    });
    const notes=value.notes.map(n=>{if(!n||typeof n.id!=='string'||typeof n.text!=='string'||typeof n.date!=='string'||!Number.isFinite(Date.parse(n.date)))throw Error('ノートの形式が不正です');return {id:n.id.slice(0,80),text:n.text.slice(0,2000),date:n.date}});
    return {version:2,favorites,alerts,notes,autoRefresh:!!value.autoRefresh};
  }
  function csv(rows){
    const cell=v=>'"'+String(v??'').replace(/^[=+@-]/,"'$&").replace(/"/g,'""')+'"';
    const columns=['key','name','cat','last','cur','last_date','chg1d','chg5d','chg20d','z1d'];
    return '\ufeff'+[columns.join(','),...rows.map(r=>columns.map(k=>typeof r[k]==='number'?r[k]:cell(r[k])).join(','))].join('\r\n');
  }
  return {finite,filterMarkets,compare,hit,fresh,backup,csv};
});
