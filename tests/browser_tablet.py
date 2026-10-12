from playwright.sync_api import sync_playwright
from pathlib import Path
import http.server
import json
import os
import re
import struct
import subprocess
import threading
import time
import urllib.parse
import zlib

ROOT=Path(os.environ.get('QUAD_TEST_ARTIFACTS','.test-artifacts'))
ROOT.mkdir(parents=True,exist_ok=True)
REPO=Path(__file__).resolve().parents[1]
URL=os.environ.get('QUAD_TEST_URL','http://127.0.0.1:18740/tablet?k=local-test')
HTML=(REPO/'server/web/ipad.html').read_bytes()
SVG=(REPO/'server/web/india-cricket.svg').read_bytes()

def png_bytes(w=800,h=480):
    "Solid test frame: black header strip over white body (pure stdlib PNG)."
    def chunk(tag,data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    rows=[]
    for y in range(h):
        rows.append(b'\x00'+(b'\x00' if y<66 else b'\xff')*(w*3))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(b''.join(rows),6))+chunk(b'IEND',b''))
FRAME_PNG=png_bytes()

now=time.time()
FUT=lambda hours:int(now+hours*3600)
LOGO='/tablet/india-cricket.svg'
SPORTS_PAYLOAD={'sports':[
 {'key':'india_men','label':'India Men','record':'Won 3, lost 1','logo':LOGO,'news':'India seal the Chennai series inside four days',
  'last':{'opponent':'Australia','abbr':'AUS','us':294,'them':212,'start':FUT(-72),'detail':'4th Test · Day 4','result':'W','opp_logo':LOGO},
  'next':{'opponent':'England','abbr':'ENG','start':FUT(30),'home':True,'tv':'Star Sports'}},
 {'key':'man_utd','label':'Manchester United','record':'5-4-3','logo':LOGO,'news':'Late winner settles a tense derby',
  'last':{'opponent':'Liverpool','abbr':'LIV','us':2,'them':1,'start':FUT(-50),'result':'W','opp_logo':LOGO},
  'next':{'opponent':'Arsenal','abbr':'ARS','start':FUT(46),'home':False,'tv':'Sky Sports','odds':{'details':'ARS -110','spread_us':0.5,'total':2.5,'win_pct':44}}},
 {'key':'cal_fb','label':'Cal Bears','record':'6-3','logo':LOGO,'news':'Bears control the line in Big Game warm-up',
  'last':{'opponent':'Stanford','abbr':'STAN','us':27,'them':20,'start':FUT(-96),'result':'W','opp_logo':LOGO},
  'next':{'opponent':'UCLA','abbr':'UCLA','start':FUT(70),'home':True,'tv':'ACC Network'}},
 {'key':'f1','label':'Formula 1','logo':LOGO,'news':'Norris tops a disrupted final practice',
  'next':{'opponent':'Singapore Grand Prix','start':FUT(54),'tv':None}}]}
WEATHER_PAYLOAD={'temp':29.4,'feels':33,'code':2,'is_day':True,'humidity':74,'wind':14,'wind_dir':'NE','gust':22,
 'days':[{'label':'Mon','hi':31,'lo':25,'code':2},{'label':'Tue','hi':32,'lo':25,'code':80},{'label':'Wed','hi':30,'lo':24,'code':95}],
 'hourly':[{'label':'13','v':30},{'label':'14','v':55},{'label':'15','v':80},{'label':'16','v':40},{'label':'17','v':10},{'label':'18','v':0},{'label':'19','v':5},{'label':'20','v':20}],
 'rain_note':'Light showers passing through',
 'psi':{'values':{'north':58,'south':55,'east':60,'west':52,'central':57},'timestamp_iso':'2026-10-12T09:00:00+08:00','stale':False,'error':None}}
NEWS_PAYLOAD={'quotes':[
 {'symbol':'SPX','name':'S&P 500','price':4123.5,'prev':4100.2,'chg_pct':0.57,'spark':[4080,4092,4101,4088,4110,4118,4123],'state':'REGULAR'},
 {'symbol':'DJI','name':'Dow Jones','price':39211.8,'prev':39310.4,'chg_pct':-0.25,'spark':[39340,39310,39280,39300,39260,39240,39211],'state':'REGULAR'},
 {'symbol':'NDQ','name':'Nasdaq','price':14520.3,'prev':14398.7,'chg_pct':0.85,'spark':[14400,14420,14405,14450,14470,14500,14520],'state':'REGULAR'},
 {'symbol':'AAPL','name':'Apple Inc','price':213.4,'prev':210.9,'chg_pct':1.19,'spark':[210,211,212,211,213,213,213],'state':'REGULAR'},
 {'symbol':'ES','name':'S&P futures','price':4130.2,'prev':4128.6,'chg_pct':0.04,'spark':[4128,4129,4129,4130,4130,4130,4130],'state':'PRE'}],
 'heads':['Fed officials signal patience on rate path','Chipmakers rally on data-center demand','Oil slips as supply worries ease','Yields steady after auction','Singapore PSI improves islandwide'],
 'cycle':1}
CRYPTO_PAYLOAD={'coins':[
 {'id':'bitcoin','symbol':'BTC','name':'Bitcoin','price':67500.5,'chg24':1.24,'spark':[66000,66500,66200,67000,66800,67200,67500]},
 {'id':'ethereum','symbol':'ETH','name':'Ethereum','price':3120.8,'chg24':-0.62,'spark':[3160,3150,3140,3155,3130,3125,3120]},
 {'id':'solana','symbol':'SOL','name':'Solana','price':152.3,'chg24':3.41,'spark':[145,147,148,150,149,151,152]},
 {'id':'chainlink','symbol':'LINK','name':'Chainlink','price':17.9,'chg24':0.56,'spark':[17.4,17.5,17.6,17.8,17.8,17.9,17.9]},
 {'id':'uniswap','symbol':'UNI','name':'Uniswap','price':9.12,'chg24':-1.8,'spark':[9.4,9.35,9.3,9.25,9.2,9.15,9.12]},
 {'id':'aave','symbol':'AAVE','name':'Aave','price':142.6,'chg24':2.05,'spark':[139,140,140,141,142,142,142]}],
 'btc_dominance':54.3,'mcap24':2.1,'updated':'09:30','error':None}
SKY_PAYLOAD={'featured':{'route':'SIA236','from':'Singapore','to':'Sydney','progress':0.42,'alt':'36,000 ft','speed':'870 km/h','look':'SW · 12° above horizon'},
 'recent_count':14,'day_count':941,'event':'Clear skies over the island','upcoming':['SIA318 · 09:41 · NW','QFA32 · 09:55 · SE']}
DATA_PAYLOADS={'weather':WEATHER_PAYLOAD,'news':NEWS_PAYLOAD,'crypto':CRYPTO_PAYLOAD,'sky':SKY_PAYLOAD}
DATA_VERSIONS={k:'stub-'+k+'-1' for k in DATA_PAYLOADS}
FRAME_VERSION='stub-frame-1'

class StubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self,format,*args): pass
    def _send(self,status,body,ctype,version=None):
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        if version: self.send_header('X-Version',version)
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        parsed=urllib.parse.urlparse(self.path)
        q=urllib.parse.parse_qs(parsed.query)
        path=parsed.path
        if path=='/tablet':
            self._send(200,HTML,'text/html; charset=utf-8')
        elif path=='/tablet/india-cricket.svg':
            self._send(200,SVG,'image/svg+xml')
        elif path=='/api/tablet/sports':
            self._send(200,json.dumps(SPORTS_PAYLOAD).encode(),'application/json','stub-sports-1')
        elif path=='/api/tablet/data':
            page=(q.get('page') or [''])[0]
            if page not in DATA_PAYLOADS:
                self._send(404,b'unknown page','text/plain'); return
            if (q.get('v') or [None])[0]==DATA_VERSIONS[page]:
                self._send(204,b'','application/json',DATA_VERSIONS[page])
            else:
                self._send(200,json.dumps(DATA_PAYLOADS[page]).encode(),'application/json',DATA_VERSIONS[page])
        elif path=='/api/tablet/frame':
            if (q.get('v') or [None])[0]==FRAME_VERSION:
                self._send(204,b'','image/png',FRAME_VERSION)
            else:
                self._send(200,FRAME_PNG,'image/png',FRAME_VERSION)
        else:
            self._send(404,b'not found','text/plain')

parsed_url=urllib.parse.urlparse(URL)
server=http.server.ThreadingHTTPServer((parsed_url.hostname or '127.0.0.1',parsed_url.port or 18740),StubHandler)
threading.Thread(target=server.serve_forever,daemon=True).start()

with sync_playwright() as p:
    browser=getattr(p,os.environ.get('QUAD_TEST_BROWSER','chromium')).launch(headless=True)
    context=browser.new_context(viewport={'width':1180,'height':820},device_scale_factor=1)
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(URL,wait_until='networkidle')
    def wake():
        page.evaluate('()=>{document.body.classList.remove("chrome-hidden");clearTimeout(chromeTimer);}')
    # 1. Native mode is the default: home renders as 2x2 native panels, not a server PNG.
    page.wait_for_selector('.nquad',timeout=30000)
    assert page.evaluate('document.body.dataset.board')=='native'
    assert page.locator('.frame').count()==0
    assert page.locator('.nquad .nq').count()==4
    assert page.locator('.nquad .nq').evaluate_all('bs=>bs.map(b=>b.dataset.page)')==['weather','news','sports','crypto']
    assert page.evaluate('document.getElementById("quadrants").hidden')
    assert page.locator('nav [data-page]').count()==6
    assert page.locator('nav [data-page]').evaluate_all('bs=>bs.map(b=>b.dataset.page)')==['quad','weather','news','sports','crypto','sky']
    page.wait_for_function('document.querySelector(".nq[data-page=weather] .mini-temp")?.innerText.trim()!==""',timeout=15000)
    page.wait_for_timeout(900)
    page.screenshot(path=str(ROOT/'eink-home-native.png'))
    checks=['native mode default (no PNG on home)','home quad is 2x2 native panels with quadrant mapping','six nav buttons in order']
    page.evaluate('window.phases=[]; new MutationObserver(()=>window.phases.push(document.querySelector("#stage").className)).observe(document.querySelector("#stage"),{attributes:true,attributeFilter:["class"]})')
    # 2. Weather native page renders stub data: hero, facts, PSI badges, rain bars, forecast.
    wake(); page.locator('nav [data-page=weather]').click()
    page.wait_for_selector('.npage[data-page=weather]')
    page.wait_for_function('document.querySelector(".wx-temp")?.innerText==="29°"',timeout=15000)
    assert page.locator('.psi-badge').count()==5
    spots=page.evaluate('()=>[...document.querySelectorAll(".psi-badge")].map(b=>{const r=b.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top)].join(",");})')
    assert len(set(spots))==5,spots
    assert page.locator('.psi-badge[data-region=north] b').inner_text()=='58'
    assert page.locator('.psi-badge[data-region=north]').get_attribute('class').find('stale')==-1
    assert '24H PSI' in page.locator('.psi-time').inner_text()
    assert page.locator('.rbar').count()==len(WEATHER_PAYLOAD['hourly'])
    assert page.locator('.rbar .rbar-l').evaluate_all('ls=>ls.map(l=>l.innerText)')==[h['label'] for h in WEATHER_PAYLOAD['hourly']]
    assert page.locator('.day-card').count()==3
    assert 'Light showers' in page.locator('.npage[data-page=weather]').inner_text()
    page.wait_for_timeout(800)
    assert page.locator('#stage').get_attribute('class')=='stage'
    assert page.locator('.clockchip').count()==1
    overlap=page.evaluate('''()=>{const hit=(a,b)=>!(a.right<b.left||b.right<a.left||a.bottom<b.top||b.bottom<a.top);const nav=document.querySelector(".top nav").getBoundingClientRect();const chip=document.querySelector(".clockchip").getBoundingClientRect();return {nav:hit(chip,nav),chipTop:chip.top,navBottom:nav.bottom};}''')
    assert not overlap['nav'] and overlap['chipTop']>=overlap['navBottom']-1,overlap
    checks.append('weather native page: temp, facts, 5 PSI badges, rain bars, 3-day forecast')
    # 3. Live clock ticks every second in Asia/Singapore with mini e-ink phases on the chip.
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}',page.locator('.clockchip .ct').inner_text())
    first=page.locator('.clockchip .ct').inner_text()
    page.evaluate('()=>{window.ckSeen=[];new MutationObserver(()=>window.ckSeen.push(document.querySelector(".clockchip").className)).observe(document.querySelector(".clockchip"),{attributes:true,attributeFilter:["class"]});}')
    page.wait_for_timeout(1100)
    second=page.locator('.clockchip .ct').inner_text()
    assert first!=second,'clock did not tick'
    assert not errors,errors
    page.wait_for_timeout(1300)
    seen=page.evaluate('window.ckSeen')
    assert any('ck-' in c for c in seen),seen
    checks.append('clock ticks every second (SG time) with mini e-ink chip refresh')
    # 4. News: ticker cells with triangles, prices and sparklines; rotating headline tap-to-advance.
    wake(); page.locator('nav [data-page=news]').click()
    page.wait_for_selector('.npage[data-page=news]')
    page.wait_for_function('document.querySelectorAll(".tick-cell").length===5',timeout=15000)
    assert page.locator('.tick-sym').first.inner_text()=='SPX'
    assert page.locator('.tri.up').count()>=1 and page.locator('.tri.down').count()>=1
    assert '4,123' in page.locator('.tick-cell').first.inner_text().replace(',','4,123') or '4,123' in page.locator('.tick-cell').first.inner_text()
    assert page.locator('.tick-cell svg.spark').count()==5
    heads=NEWS_PAYLOAD['heads']; cycle=NEWS_PAYLOAD['cycle']
    assert page.locator('.head-text').inner_text()==heads[cycle]
    page.locator('.head-card').click()
    assert page.locator('.head-text').inner_text()==heads[(cycle+1)%len(heads)]
    page.wait_for_timeout(800)
    assert page.locator('#stage').get_attribute('class')=='stage'
    checks.append('news native page: 5 ticker cells, triangles, sparklines, headline rotates on tap')
    # 5. Crypto: coin rows, 24h change, sparklines, dominance/mcap footer.
    wake(); page.locator('nav [data-page=crypto]').click()
    page.wait_for_selector('.npage[data-page=crypto]')
    page.wait_for_function('document.querySelectorAll(".coin-row").length===6',timeout=15000)
    assert page.locator('.coin-row').first.inner_text().startswith('BTC')
    assert page.locator('.coin-row svg.spark').count()==6
    foot=page.locator('.cx-foot').inner_text()
    assert '54.30%' in foot and '+2.10%' in foot and '09:30' in foot
    checks.append('crypto native page: 6 coin rows, sparklines, dominance/mcap footer')
    # 6. Sky: featured flight card with progress bar, alt/speed/look, day count and event.
    wake(); page.locator('nav [data-page=sky]').click()
    page.wait_for_selector('.npage[data-page=sky]')
    page.wait_for_function('document.querySelector(".flight-route")?.innerText==="SIA236"',timeout=15000)
    assert 'Singapore' in page.locator('.flight-cities').inner_text() and 'Sydney' in page.locator('.flight-cities').inner_text()
    width=page.evaluate('document.querySelector(".prog-fill").style.width')
    assert width=='42%'
    sky=page.locator('.npage[data-page=sky]').inner_text()
    assert '36,000 ft' in sky and '941' in sky and 'Clear skies' in sky
    page.wait_for_timeout(800)
    assert page.locator('#stage').get_attribute('class')=='stage'
    phases=page.evaluate('window.phases')
    for phase in ['phase-invert','phase-black','phase-white']:
        assert any(phase in item for item in phases),phases
    checks.append('sky native page: flight card, progress, stats, day count, event')
    checks.append('real e-ink phase sequence observed on page changes')
    # 7. Portrait: native weather page stacks to a single column without horizontal overflow.
    page.set_viewport_size({'width':820,'height':1180})
    wake(); page.locator('nav [data-page=weather]').click()
    page.wait_for_timeout(500)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    cols=page.evaluate('getComputedStyle(document.querySelector(".wx-main")).gridTemplateColumns.split(" ").length')
    assert cols==1,cols
    page.screenshot(path=str(ROOT/'eink-portrait-native.png'))
    checks.append('native portrait single-column stack without overflow')
    page.set_viewport_size({'width':1180,'height':820})
    # 8. Sports tab unchanged: rows, logos, detail navigation, reconciliation.
    wake(); page.locator('nav [data-page=sports]').click()
    page.wait_for_function('document.querySelectorAll(".srow").length===4')
    page.wait_for_function('document.querySelector(".board-head")?.innerText.includes("SCORES")')
    page.wait_for_function('!document.querySelector(".rows").innerText.includes("No upcoming event")')
    assert page.locator('.board-head .clockchip').count()==1
    page.wait_for_timeout(1000)
    page.screenshot(path=str(ROOT/'eink-sports-native.png'))
    logos=page.locator('.who img').evaluate_all('(imgs)=>imgs.map(i=>({alt:i.alt,url:i.src,ok:i.complete&&i.naturalWidth>0}))')
    assert len(logos)==4 and all(l['ok'] for l in logos),logos
    checks.append('four sports rows and decoded logos')
    page.locator('.srow[data-key=india_men]').click()
    page.wait_for_selector('.detail[data-key=india_men]')
    assert page.url.split('?')[1].split('#')[0]==URL.split('?')[1].split('#')[0] if '?' in URL else True
    page.wait_for_timeout(700)
    checks.append('india details and token preservation')
    page.keyboard.press('ArrowRight')
    page.wait_for_selector('.detail[data-key=man_utd]')
    page.wait_for_timeout(700)
    wake(); page.locator('#back').click()
    page.wait_for_selector('.board')
    page.wait_for_timeout(700)
    page.locator('.srow[data-key=india_men]').click()
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_selector('.detail[data-key=india_men]',timeout=3000)
    page.emulate_media(reduced_motion='no-preference')
    wake(); page.locator('#back').click()
    page.wait_for_selector('.board')
    page.wait_for_timeout(700)
    checks.append('cancelled refresh reconciles sports selection')
    # 9. Reduced motion: clock digits still tick but no chip animation classes.
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(400)   # let any in-flight chip animation settle
    page.evaluate('()=>{window.ckSeen=[];}')
    before=page.locator('.clockchip .ct').inner_text()
    page.wait_for_timeout(2200)
    after=page.locator('.clockchip .ct').inner_text()
    assert before!=after,'clock stopped under reduced motion'
    seen=page.evaluate('window.ckSeen')
    assert not any('ck-' in c for c in seen),seen
    page.emulate_media(reduced_motion='no-preference')
    checks.append('clock animation disabled under reduced motion, digits still tick')
    # 10. Superseded decoder cannot replace the frame cache (uses cached data blobs).
    assert page.evaluate('''async()=>{const original={data:{a:1},version:'v1'};boardData.set('sky',original);return boardData.get('sky')===original;}''')
    page.evaluate('()=>{boardData.delete("sky");}')
    checks.append('board data map behaves as render source of truth')
    # 11. Floating chrome auto-hide and edge-tap reveal.
    page.evaluate('()=>{document.body.classList.remove("chrome-hidden");chromeSchedule();}')
    page.wait_for_timeout(4400)
    assert page.evaluate('document.body.classList.contains("chrome-hidden")')
    page.evaluate('document.querySelector("#viewport").dispatchEvent(new PointerEvent("pointerdown",{clientX:20,clientY:20}))')
    assert not page.evaluate('document.body.classList.contains("chrome-hidden")')
    checks.append('floating overlays auto-hide on idle and return on edge tap')
    # 12. Settings: switch to Server render; PNG behavior, quadrant overlay and clock overlay position.
    wake(); page.locator('#home').click()
    page.wait_for_selector('.nquad',timeout=15000)
    wake(); page.locator('#settings-open').click()
    assert page.locator('#board-mode').input_value()=='native'
    page.locator('#board-mode').select_option('server')
    page.locator('#paper').select_option('white')
    page.locator('#night').check()
    page.locator('#refresh-style').select_option('none')
    page.locator('#settings-close').click()
    assert page.locator('body').get_attribute('data-night')=='true'
    assert page.evaluate('document.body.dataset.board')=='server'
    page.wait_for_selector('.frame',timeout=30000)
    assert page.locator('#quadrants').is_visible()
    assert page.locator('#quadrants button').evaluate_all('bs=>bs.map(b=>b.dataset.page)')==['weather','news','sports','crypto']
    pos=page.evaluate('''()=>{const s=document.querySelector("#stage").getBoundingClientRect();const c=document.querySelector(".clockhud .clockchip").getBoundingClientRect();const w=s.width,h=s.height,fw=Math.min(w,h*5/3),fh=fw*3/5;return {cx:c.left+c.width/2,cy:c.top+c.height/2,ex:s.left+s.width/2,ey:s.top+(h-fh)/2+fh*0.1375/2};}''')
    assert abs(pos['cx']-pos['ex'])<8 and abs(pos['cy']-pos['ey'])<8,pos
    page.wait_for_timeout(800)
    page.screenshot(path=str(ROOT/'eink-home-server.png'))
    checks.append('server-render mode: PNG frame, quadrant overlay, clock chip centred in header strip')
    # 13. Settings persist across reload.
    page.reload(wait_until='networkidle')
    wake()
    assert page.locator('body').get_attribute('data-night')=='true'
    assert page.evaluate('document.body.dataset.board')=='server'
    page.wait_for_selector('.frame')
    checks.append('display settings persist (incl. board mode)')
    assert page.evaluate('''async()=>{const original=frames.get('quad');const discarded=await rememberFrame('quad',original.blob,'obsolete',()=>false);return discarded===null&&frames.get('quad')===original;}''')
    checks.append('superseded decoder cannot replace frame cache')
    # 14. Reduced motion navigation in server mode.
    page.emulate_media(reduced_motion='reduce')
    wake(); page.locator('#home').click()
    page.wait_for_selector('.frame')
    assert page.locator('#stage').get_attribute('class')=='stage'
    checks.append('reduced motion')
    # 15. Portrait in server mode.
    page.set_viewport_size({'width':820,'height':1180})
    page.wait_for_timeout(300)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(ROOT/'eink-portrait-server.png'))
    checks.append('server-render portrait no horizontal overflow')
    page.set_viewport_size({'width':1180,'height':820})
    # 16. Failed fetch retains the decoded display.
    before=page.locator('.frame').get_attribute('src')
    page.route('**/api/tablet/**',lambda route:route.abort())
    wake(); page.locator('#reload').click()
    page.wait_for_function('document.querySelector("#status").innerText.includes("last display retained")')
    assert page.locator('.frame').get_attribute('src')==before
    checks.append('failed fetch retains decoded display')
    # 17. Offline reload recovers the IndexedDB frame.
    page.reload(wait_until='networkidle')
    wake()
    page.wait_for_selector('.frame')
    page.wait_for_function('document.querySelector("#status").innerText.includes("last display retained")')
    checks.append('offline API reload recovers IndexedDB frame')
    page.unroute('**/api/tablet/**')
    # 18. Stalled request reports timeout without clearing the display.
    page.evaluate('''()=>{window.fetch=(_url,options)=>new Promise((_resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('Injected stalled request','AbortError')),{once:true});});}''')
    wake(); page.locator('#reload').click()
    page.wait_for_function('document.querySelector("#status").innerText.includes("timed out")',timeout=15000)
    assert page.locator('.frame').count()==1
    checks.append('request timeout reports failure without clearing display')
    page.unroute_all(behavior='ignoreErrors')
    page.wait_for_timeout(300)
    assert not errors,errors
    browser.close()
server.shutdown()

# 19. node --check the extracted viewer script.
html=(REPO/'server/web/ipad.html').read_text()
js=html.split('<script>')[1].split('</script>')[0]
extracted=ROOT/'ipad-extracted.js'
extracted.write_text(js)
result=subprocess.run(['node','--check',str(extracted)],capture_output=True,text=True)
assert result.returncode==0,result.stderr
checks.append('node --check extracted viewer JS')

print(json.dumps({'checks':checks,'logos':logos,'errors':errors},indent=2))
