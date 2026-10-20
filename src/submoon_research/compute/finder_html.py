"""Локальная HTML-страница результатов vast_find (без сервера и внешних ресурсов)."""
import json
from pathlib import Path

from submoon_research.compute.finder import FLAG_LABELS, REJECTION_LABELS

TEMPLATE = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vast.ai: __TITLE__</title>
<style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6a65;--line:#e3e1db;--head:#f1efea;--accent:#2f6fde;--good:#1f7a4a;--warn:#a5621a;--bad:#b3342b;--row:#f6f4ef}
@media (prefers-color-scheme: dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--muted:#a29f97;--line:#34332f;--head:#252522;--accent:#6c9cf0;--good:#5cc28a;--warn:#e0a35a;--bad:#ef7b70;--row:#22221f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:16px 20px 8px}h1{font-size:18px;margin:0 0 4px}.meta{color:var(--muted);font-size:13px}
.warn{color:var(--bad);font-weight:600}
.controls{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:8px 20px 12px;border-bottom:1px solid var(--line)}
.controls label{display:flex;gap:6px;align-items:center;font-size:13px}
input[type=text],input[type=number]{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font:inherit}
input[type=number]{width:80px}
.wrap{overflow-x:auto;padding:0 12px 24px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--head);cursor:pointer;user-select:none;font-weight:600;font-size:12.5px}
th.l,td.l{text-align:left}th:hover{color:var(--accent)}
tr:hover td{background:var(--row)}tr.late td{color:var(--muted)}
.chip{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 6px;margin:0 2px;font-size:11.5px;color:var(--muted)}
.chip.bad{border-color:var(--bad);color:var(--bad)}.chip.warn{border-color:var(--warn);color:var(--warn)}
.est{color:var(--muted)}button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--head);color:var(--fg);border-radius:6px;padding:2px 8px;cursor:pointer}
footer{padding:0 20px 24px;color:var(--muted);font-size:12.5px}
</style></head><body>
<header><h1 id="title"></h1><div class="meta" id="meta"></div></header>
<div class="controls">
<label>CPU <input type="text" id="q" placeholder="9654, 7995wx, i9…"></label>
<label>макс. $/ч <input type="number" id="maxp" step="0.01" min="0"></label>
<label><input type="checkbox" id="whole"> только целые машины</label>
<label><input type="checkbox" id="verified"> только verified</label>
<label><input type="checkbox" id="known"> только CPU из каталога</label>
<label><input type="checkbox" id="exact"> без грубых оценок (~)</label>
<label id="fitwrap"><input type="checkbox" id="fit"> укладываются в срок</label>
<span class="meta" id="count"></span>
</div>
<div class="wrap"><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<footer id="foot"></footer>
<script>
const DATA = __DATA__;
const FLAGS = __FLAGS__;
const REJ = __REJ__;
const hasEst = !!DATA.profile.workload;
const cols = [
  {k:'rank', t:'#', v:r=>r.rank},
  ...(hasEst ? [{k:'usd', t:'$ на серию', v:r=>r.estimate?r.estimate.usd[1]:null, f:r=>r.estimate?span(r.estimate.usd,1):'–'},
                {k:'hours', t:'часы', v:r=>r.estimate?r.estimate.hours[1]:null, f:r=>r.estimate?span(r.estimate.hours,1)+(r.estimate.bound==='path'?' <span class="est">путь</span>':''):'–'}] : []),
  {k:'cpu', t:'CPU', l:1, v:r=>r.cpu_key||'', f:r=>esc(r.cpu_key||'')},
  {k:'speed', t:'ядро', v:r=>r.core_speed, f:r=>r.core_speed==null?'?':r.core_speed.toFixed(2)+({measured:'',model:'*',family:'~'}[r.speed_confidence]||'')},
  {k:'vcpu', t:'vCPU', v:r=>r.vcpu, f:r=>r.host_vcpu?fmt(r.vcpu,0)+' / '+fmt(r.host_vcpu,0):fmt(r.vcpu,0)},
  {k:'share', t:'доля', v:r=>r.share, f:r=>r.share==null?'–':Math.round(r.share*100)+'%'},
  {k:'ram', t:'RAM ГБ', v:r=>r.ram_gb, f:r=>fmt(r.ram_gb,0)},
  {k:'price', t:'$/ч', v:r=>r.usd_per_hour, f:r=>fmt(r.usd_per_hour,3)},
  {k:'ref', t:'$/эт.ядро·ч', v:r=>r.usd_per_ref_core_hour, f:r=>fmt(r.usd_per_ref_core_hour,4)},
  {k:'ppc', t:'$/vCPU·ч', v:r=>r.usd_per_vcpu_hour, f:r=>fmt(r.usd_per_vcpu_hour,4)},
  {k:'ver', t:'проверка', l:1, v:r=>r.verification||''},
  {k:'rel', t:'надёжн.', v:r=>r.reliability, f:r=>fmt(r.reliability,3)},
  {k:'gpu', t:'GPU', l:1, v:r=>r.gpu_name||'', f:r=>(r.num_gpus?r.num_gpus+'× ':'')+esc(r.gpu_name||'')},
  {k:'loc', t:'место', l:1, v:r=>r.location||'', f:r=>esc(r.location||'')},
  {k:'seen', t:'машина видна с', l:1, v:r=>r.history?r.history.machine_first_seen:'', f:r=>r.history&&r.history.machine_first_seen?r.history.machine_first_seen.replace('T',' ').slice(0,16):''},
  {k:'offer', t:'offer', v:r=>r.offer_id, f:r=>'<button title="Скопировать ID" data-id="'+r.offer_id+'">'+r.offer_id+'</button>'},
  {k:'flags', t:'флаги', l:1, v:r=>r.flags.join(','), f:r=>r.flags.filter(f=>f!=='speed_estimate'&&f!=='family_estimate').map(chip).join('')},
];
let sortKey='rank', asc=true;
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function fmt(v,d){return v==null?'–':Number(v).toFixed(d)}
function span(p,d){return Math.abs(p[1]-p[0])<Math.pow(10,-d)?p[0].toFixed(d):p[0].toFixed(d)+'–'+p[1].toFixed(d)}
function chip(f){const bad=['over_deadline','deverified','unknown_cpu'].includes(f), warn=['slice','unverified','low_reliability','ram_limits_workers'].includes(f);
  return '<span class="chip'+(bad?' bad':warn?' warn':'')+'">'+esc(FLAGS[f]||f)+'</span>'}
function head(){document.getElementById('head').innerHTML=cols.map(c=>'<th class="'+(c.l?'l':'')+'" data-k="'+c.k+'">'+c.t+(c.k===sortKey?(asc?' ▲':' ▼'):'')+'</th>').join('')}
function render(){
  const q=document.getElementById('q').value.trim().toLowerCase(), maxp=parseFloat(document.getElementById('maxp').value);
  const whole=document.getElementById('whole').checked, ver=document.getElementById('verified').checked,
        known=document.getElementById('known').checked, exact=document.getElementById('exact').checked, fit=document.getElementById('fit').checked;
  const col=cols.find(c=>c.k===sortKey);
  let rows=DATA.rows.filter(r=>(!q||((r.cpu_name||'')+' '+(r.cpu_key||'')).toLowerCase().includes(q))
    &&(isNaN(maxp)||r.usd_per_hour<=maxp)&&(!whole||(r.share!=null&&r.share>=0.9))
    &&(!ver||r.verification==='verified')&&(!known||r.core_speed!=null)&&(!exact||(r.core_speed!=null&&r.speed_confidence!=='family'))&&(!fit||!r.flags.includes('over_deadline')));
  rows.sort((a,b)=>{let x=col.v(a),y=col.v(b);if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;
    const s=typeof x==='string'?x.localeCompare(y):x-y;return asc?s:-s});
  document.getElementById('body').innerHTML=rows.map(r=>'<tr class="'+(r.flags.includes('over_deadline')?'late':'')+'" title="'+esc(r.cpu_name||'')+'">'+
    cols.map(c=>'<td class="'+(c.l?'l':'')+'">'+(c.f?c.f(r):esc(c.v(r)==null?'–':c.v(r)))+'</td>').join('')+'</tr>').join('');
  document.getElementById('count').textContent='показано '+rows.length+' из '+DATA.rows.length;
}
document.getElementById('head').addEventListener('click',e=>{const k=e.target.closest('th')?.dataset.k;if(!k)return;
  if(k===sortKey)asc=!asc;else{sortKey=k;asc=true}head();render()});
document.getElementById('body').addEventListener('click',e=>{const id=e.target.dataset&&e.target.dataset.id;if(!id)return;
  try{navigator.clipboard.writeText(id);e.target.textContent='скопировано';setTimeout(()=>{e.target.textContent=id},1200)}catch(_){}});
['q','maxp','whole','verified','known','exact','fit'].forEach(id=>document.getElementById(id).addEventListener('input',render));
const p=DATA.profile, s=DATA.search;
document.getElementById('title').textContent='Vast.ai — '+p.title+' ('+p.name+')';
const status=s.mode==='sweep'?'<span class="warn">ограниченный поиск, без гарантии полноты</span>: проходы '+s.passes.map(x=>esc(x.name)+' ['+esc(x.status||'unknown')+'] +'+x.new).join(', ')+' · запросов '+s.queries+' за '+Math.round(s.elapsed_seconds)+' с · остановка: '+esc(s.stop_reason||'unknown')+
  (s.last_two_passes_new_fraction!=null?' · последние два выполненных прохода добавили '+(100*s.last_two_passes_new_fraction).toFixed(1)+'%':' · насыщение не оценено'):'воспроизведение сохранённых данных';
document.getElementById('meta').innerHTML=esc(DATA.utc.replace('T',' '))+' UTC · '+status+' · предложений '+s.raw_offers+', подходит '+DATA.rows.length+
  (s.errors.length?' · <span class="warn">ошибки: '+esc(s.errors.join(', '))+'</span>':'');
if(!hasEst)document.getElementById('fitwrap').style.display='none';
const rej=Object.entries(DATA.rejected).map(([k,v])=>esc(REJ[k]||k)+' — '+v).join('; ');
document.getElementById('foot').innerHTML='Сортировка — щелчок по заголовку. Скорость ядра (P-ядро i9-14900K = 1.00): без значка — замер, * — оценка модели, ~ — грубая оценка семейства (configs/compute/cpu_catalog.yaml; опора: '+esc(DATA.catalog_reference||'')+').'+
  (hasEst?' Оценка часов и $: профиль '+esc(p.name)+(p.source?' ('+esc(p.source)+')':'')+'; «путь» — время задаёт самое долгое задание, а не число ядер.':'')+
  '<br>$/эт.ядро·ч — цена часа одного эталонного ядра: $/ч ÷ (физические ядра доли × скорость ядра).'+
  '<br>Запрос к серверу: <code>'+esc(DATA.server_query)+'</code>'+(rej?'<br>Отсеяно: '+rej:'')+
  '<br>Цены и конфигурация заявлены провайдером; фактическое железо проверяет базовая проверка после аренды.';
head();render();
</script></body></html>
'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str).replace('</', '<\\/')


def write_html(path, result):
    data = {k: v for k, v in result.items() if k != 'raw'}
    text = (TEMPLATE.replace('__TITLE__', result['profile']['title'].replace('<', ''))
            .replace('__FLAGS__', _json(FLAG_LABELS)).replace('__REJ__', _json(REJECTION_LABELS))
            .replace('__DATA__', _json(data)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)
    return path
