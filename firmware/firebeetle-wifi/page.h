#pragma once

static const char kPlaygroundPage[] PROGMEM = R"HTML(<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FireBeetle playground</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#101b1a;color:#eef5e9}
*{box-sizing:border-box}body{margin:0;padding:28px 20px 48px}main{max-width:680px;margin:auto}
.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#b8d0bd}h1{font-size:clamp(34px,7vw,52px);line-height:1.06;letter-spacing:-.05em;margin:14px 0}
.intro{color:#b8c7bf;line-height:1.6;max-width:48ch}.connection{display:inline-flex;gap:9px;align-items:center;font-size:13px;padding:9px 13px;border:1px solid #3a5145;border-radius:30px;margin:12px 0}
.dot{width:8px;height:8px;background:#bded81;border-radius:50%}.offline .dot{background:#efac80}
.panel{background:#1a2925;border:1px solid #34473c;padding:24px;border-radius:22px;margin-top:18px}h2{font-size:19px;margin:0 0 8px}p{margin:8px 0 20px;color:#b8c7bf;line-height:1.5}
.controls{display:flex;gap:10px;flex-wrap:wrap}button{font:inherit;font-size:15px;color:inherit;background:#263a30;border:1px solid #4b6150;border-radius:12px;padding:12px 18px;cursor:pointer;touch-action:manipulation}
button:hover{background:#344c3c}button[aria-pressed=true]{background:#c4ef91;color:#172519;border-color:#c4ef91}button:focus-visible{outline:3px solid #f1cd85;outline-offset:3px}
.color{min-width:80px}.color::before{content:'';display:inline-block;vertical-align:middle;width:10px;height:10px;border-radius:50%;background:var(--swatch);margin-right:8px}
.readouts{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:22px}.label{font-size:12px;color:#b8c7bf;display:block;margin-bottom:5px}.value{font-size:22px;font-variant-numeric:tabular-nums}.small{font-size:13px;color:#b8c7bf;overflow-wrap:anywhere}
#feedback{min-height:24px;margin:16px 0 0;color:#c4ef91;font-size:14px}footer{font-size:13px;color:#94ac9d;margin-top:22px;line-height:1.6}
</style></head><body><main>
<div class="eyebrow">FireBeetle · Wi-Fi playground</div><h1>A tiny board,<br>under your control.</h1>
<p class="intro">This page came straight from the board. Tap a button and watch the real LEDs respond.</p>
<div class="connection" id="connection" role="status"><span class="dot"></span><span id="connectionText">Connecting to your board…</span></div>
<section class="panel"><h2>The green light</h2><p>The small green LED beside the USB connector.</p>
<div class="controls" id="ledControls"><button data-mode="off" aria-pressed="false">Off</button><button data-mode="on" aria-pressed="false">On</button><button data-mode="blink" aria-pressed="false">Blink</button></div>
</section>
<section class="panel"><h2>A little colour</h2><p>The square RGB light below the battery socket. Or press the board’s button marked 27 to cycle colours.</p>
<div class="controls" id="rgbControls"><button class="color" style="--swatch:#ff8982" data-color="red" aria-pressed="false">Red</button><button class="color" style="--swatch:#9ad99b" data-color="green" aria-pressed="false">Green</button><button class="color" style="--swatch:#8cbcff" data-color="blue" aria-pressed="false">Blue</button><button class="color" style="--swatch:#d1a0ef" data-color="purple" aria-pressed="false">Purple</button><button data-color="off" aria-pressed="false">Off</button></div>
<div id="feedback" role="status" aria-live="polite"></div>
</section>
<section class="panel"><h2>Inside the board</h2><div class="readouts">
<div><span class="label">Time awake</span><span class="value" id="uptime">—</span></div>
<div><span class="label">Free memory</span><span class="value" id="heap">—</span></div>
<div><span class="label">Connected devices</span><span class="value" id="clients">—</span></div>
<div><span class="label">Button presses</span><span class="value" id="presses">—</span></div>
</div><p class="small" id="identity" style="margin:22px 0 0">Reading device information…</p></section>
<footer>Runs entirely on your FireBeetle. No internet is needed for this page.<br>The orange charging light works independently of these controls.</footer>
</main><script>
const $=id=>document.getElementById(id);
function render(s){
  $('connection').classList.remove('offline');$('connectionText').textContent='Connected · '+s.ip;
  let sec=Math.floor(s.uptime_ms/1000);$('uptime').textContent=Math.floor(sec/60)+'m '+String(sec%60).padStart(2,'0')+'s';
  $('heap').textContent=Math.round(s.free_heap/1024)+' KB';$('clients').textContent=s.clients;$('presses').textContent=s.button_presses;
  $('identity').textContent=s.chip+' · '+s.flash_mb+' MB flash · ID '+s.device_id;
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===s.led_mode)));
  document.querySelectorAll('[data-color]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.color===s.rgb)));
}
function offline(){ $('connection').classList.add('offline');$('connectionText').textContent='Reconnect to FireBeetle-Play'; }
async function update(){try{const r=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});if(!r.ok)throw Error();render(await r.json());}catch(e){offline();}finally{setTimeout(update,1000);}}
async function control(path,key,value){try{const r=await fetch(path,{method:'POST',body:new URLSearchParams({[key]:value}),signal:AbortSignal.timeout(4000)});if(!r.ok)throw Error();render(await r.json());$('feedback').textContent=key==='mode'?'Green light: '+value:'Colour: '+value;}catch(e){offline();$('feedback').textContent='Could not reach the board. Check your Wi-Fi connection.';}}
$('ledControls').addEventListener('click',e=>{const b=e.target.closest('[data-mode]');if(b)control('/api/led','mode',b.dataset.mode);});
$('rgbControls').addEventListener('click',e=>{const b=e.target.closest('[data-color]');if(b)control('/api/rgb','color',b.dataset.color);});
update();
</script></body></html>)HTML";
