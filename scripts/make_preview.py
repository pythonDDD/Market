"""Create a self-contained snapshot of the real UI. Never fabricates market data."""
import copy
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
def main():
    docs=ROOT/'docs'
    latest=json.loads((docs/'data/latest.json').read_text(encoding='utf-8'))
    history=json.loads((docs/'data/history.json').read_text(encoding='utf-8'))
    if latest.get('mode') in ('mock','demo'):
        raise ValueError('Use actual data for the snapshot')
    latest=copy.deepcopy(latest)
    latest['mode']='snapshot'
    status={'version':2,'state':'preview','message':'プレビューではLINEの診断・送信を行いません。本番の状態は、改修版を反映したサイトまたはActionsで確認してください。','checked_at':latest['generated_at'],'daily_hour_jst':9,'app_month_count':None,'app_month_cap':190,'quota':{},'delivery_confirmed':False}
    text=(docs/'index.html').read_text(encoding='utf-8')
    text=re.sub(r'<link rel="stylesheet" href="([^"?]+)(?:\?[^"\s]+)?">',lambda m:'<style>'+ (docs/m[1]).read_text(encoding='utf-8')+'</style>',text)
    text=re.sub(r'<script src="([^"?]+)(?:\?[^"\s]+)?"></script>',lambda m:'<script>'+ (docs/m[1]).read_text(encoding='utf-8').replace('</script','<\\/script')+'</script>',text)
    # Embed the same dataset and use the application's normal fetch/render paths.
    payload=json.dumps({'latest':latest,'history':history,'status':status},ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('</','<\\/')
    shim='''<script>
    const SNAPSHOT = '''+payload+''';
    window.fetch = async function(url) {
      const name = String(url);
      const value = name.includes('latest.json') ? SNAPSHOT.latest : name.includes('history.json') ? SNAPSHOT.history : name.includes('line_status.json') ? SNAPSHOT.status : null;
      return value ? new Response(JSON.stringify(value), {status:200,headers:{'Content-Type':'application/json'}}) : new Response('',{status:404});
    };
    </script>'''
    text=text.replace('<head>','<head>'+shim,1)
    text=text.replace('<title>潮目 —','<title>保存データのプレビュー｜潮目 —')
    text=re.sub(r'<link rel="(?:icon|apple-touch-icon)"[^>]*>', '',text)
    (ROOT/'PREVIEW.html').write_text(text,encoding='utf-8')
    print('Created PREVIEW.html; snapshot timestamp:', latest['generated_at'])

if __name__=='__main__':main()
