"use strict";
// Atlas browser client. Real values come exclusively from /api/rows, not demo arrays.
const $=id=>document.getElementById(id);
const COLORS=["#3271df","#e38b43","#16a899","#8f64cf","#d4557b","#5c9c43","#7c879a","#c09343"];
const state={families:[],overview:null,geographies:[],selected:new Set(),lastResponse:null,chartType:"line",pending:0};
const fmt=n=>Number(n||0).toLocaleString("en-US");
const short=n=>Number(n).toLocaleString("en-US",{maximumFractionDigits:4});
const pretty=s=>String(s||"").replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase());
const el=(tag,cls,text)=>{const a=document.createElement(tag);if(cls)a.className=cls;if(text!==undefined)a.textContent=text;return a;};
const api=async(path,params={})=>{
 const url=new URL(path,location.origin);
 Object.entries(params).forEach(([k,v])=>{if(v!==undefined&&v!==null)url.searchParams.set(k,String(v))});
 const r=await fetch(url,{cache:"no-store"});
 let j;try{j=await r.json()}catch{throw Error("Atlas returned an unreadable response")}
 if(!r.ok)throw Error(j.error||"The Atlas query failed");
 return j;
};
const currentFamily=()=>state.families.find(f=>f.id===$("family").value);
function notice(message){const n=$("notice");n.textContent=message||"";n.hidden=!message;}
function showError(e){notice(e.message||String(e));$("chart").replaceChildren(el("div","chart-empty","The selection could not be displayed."));}
function mode(view){
 const explore=view==="explore";
 $("nav-explore").classList.toggle("selected",explore);
 $("nav-catalog").classList.toggle("selected",!explore);
 $("explore-view").hidden=!explore;$("catalog-view").hidden=explore;
 $("page-title").textContent=explore?"Explore the economic landscape":"Atlas source collections";
 $("breadcrumb-section").textContent=explore?"Data explorer":"Source catalog";
 $("intro-desc").textContent=explore?
  "Visualize real observations from Atlas’s consolidated data warehouse. Browse indicators, compare geographies, explore time periods, and inspect underlying records.":
  "Find the data Atlas has consolidated, inspect its source-native layers, and open any available collection in the visual explorer.";
}
function selectOptions(node,records,placeholder){
 node.replaceChildren();
 if(!records.length){node.add(new Option(placeholder,""));return;}
 for(const r of records)node.add(new Option(r.label,r.value));
}
function populateOverview(){
 const x=state.overview;
 $("stat-records").textContent=fmt(x.canonical_records);
 $("stat-families").textContent=fmt(x.canonical_families);
 $("stat-native").textContent=fmt(x.native_records);
 $("stat-native-sources").textContent=fmt(x.native_sources);
 $("snapshot").textContent=x.snapshots?.[0]?.snapshot_id||"Existing SQLite";
}
function drawCatalog(){
 const container=$("catalog-grid");container.replaceChildren();
 for(const f of state.families){
  const card=el("article","catalog-card");
  card.append(el("div","catalog-eyebrow",f.layer==="source_native"?"SOURCE-NATIVE / UNHARMONIZED":"SOURCE-SEPARATED / CANONICAL"));
  card.append(el("h3","",f.name===f.id?pretty(f.name):f.name));
  const stats=el("div","catalog-stats");
  for(const entry of [fmt(f.observations)+" rows",(f.series??"—")+" series",
      f.first_year!=null?f.first_year+"–"+f.last_year:"Native periods"]){
   const span=el("span");span.append(el("b","",entry));stats.append(span);
  }
  card.append(stats);
  card.append(el("p","",f.layer==="source_native"?
   "Original provider records are indexed but not harmonized. Suppressed and ambiguous values remain marked.":
   "Queryable source-separated records. Compare definitions and units before interpreting differences."));
  const b=el("button","","Explore this collection");
  b.addEventListener("click",async()=>{
   $("family").value=f.id;mode("explore");await loadFamily();window.scrollTo({top:0,behavior:"smooth"});
  });
  card.append(b);container.append(card);
 }
}
function fillFamilies(){
 const ordered=[...state.families].sort((a,b)=>a.layer.localeCompare(b.layer)||b.observations-a.observations);
 selectOptions($("family"),ordered.map(x=>({value:x.id,label:(x.layer==="source_native"?"[Native] ":"")+pretty(x.name)})),"No collections available");
 const preferred=ordered.find(x=>x.id==="wdi"&&x.layer==="source_separated")||ordered[0];
 if(preferred)$("family").value=preferred.id;
}
async function loadFamily(){
 const f=currentFamily();
 state.geographies=[];state.selected.clear();state.lastResponse=null;
 $("search-indicator").value="";$("geo-search").value="";
 $("source-caption").textContent=f?.layer==="source_native"?
  "Source-native records; original definitions and units are not harmonized.":
  "Source-separated values; not a harmonized statistical panel.";
 if(!f){$("chart").replaceChildren(el("div","chart-empty","No source found."));return;}
 await loadSeries();
}
async function loadSeries(query=""){
 const f=currentFamily();if(!f)return;
 const req=++state.pending;
 $("series").replaceChildren(new Option("Searching available indicators…",""));
 try{
  const results=await api("/api/series",{family:f.id,layer:f.layer,q:query});
  if(req!==state.pending)return;
  selectOptions($("series"),results.filter(x=>x.name).map(x=>({
   value:x.name,label:x.name+" ("+fmt(x.rows)+" rows)"})),"No matching indicators");
  const choices=[...$("series").options];
  const preferred=choices.find(x=>x.value==="NY.GDP.MKTP.KD.ZG")||choices[0];
  if(preferred)$("series").value=preferred.value;
  await loadGeographies();
 }catch(e){if(req===state.pending)showError(e);}
}
async function loadGeographies(){
 const f=currentFamily(),series=$("series").value;
 state.selected.clear();state.geographies=[];state.lastResponse=null;notice("");
 if(!f||!series){render();return;}
 try{
  state.geographies=(await api("/api/geographies",{family:f.id,layer:f.layer,series})).filter(x=>x.code);
  const preferred=["USA","GBR","DEU","GHA","FRA","CAN"].filter(code=>state.geographies.some(x=>x.code===code));
  for(const code of preferred.slice(0,3))state.selected.add(code);
  if(!state.selected.size)for(const x of state.geographies.slice(0,3))state.selected.add(x.code);
  renderGeographies();await apply();
 }catch(e){showError(e);}
}
function renderGeographies(){
 const container=$("geo-options"),q=$("geo-search").value.trim().toLowerCase();
 container.replaceChildren();
 const matches=state.geographies.filter(x=>x.code.toLowerCase().includes(q)).slice(0,80);
 if(!matches.length){container.append(el("div","geo-empty","No matching geographic codes"));return;}
 for(const geo of matches){
  const label=el("label","geo-item"),input=document.createElement("input");
  input.type="checkbox";input.checked=state.selected.has(geo.code);
  input.addEventListener("change",()=>{
   if(input.checked&&state.selected.size>=8){input.checked=false;notice("Choose at most eight geographies.");return;}
   if(input.checked)state.selected.add(geo.code);else state.selected.delete(geo.code);
  });
  label.append(input,el("b","",geo.code),el("span","",fmt(geo.rows)+" rows"));
  container.append(label);
 }
}
async function apply(){
 const f=currentFamily(),series=$("series").value;
 if(!f||!series){state.lastResponse=null;render();return;}
 const start=Number($("year-start").value),end=Number($("year-end").value);
 if(!Number.isInteger(start)||!Number.isInteger(end)||start>end){notice("Choose a valid year range.");return;}
 if(!state.selected.size){notice("Select at least one geography to chart.");return;}
 const req=++state.pending;
 $("chart").replaceChildren(el("div","chart-empty","Loading source observations…"));
 $("plot-title").textContent=series;
 $("plot-subtitle").textContent=pretty(f.name);
 $("plot-layer").textContent=f.layer==="source_native"?"Source-native data · Not harmonized":"Canonical source observations · Native units";
 try{
  const data=await api("/api/rows",{family:f.id,layer:f.layer,series,
   geos:[...state.selected].join(","),start,end,limit:2000});
  if(req!==state.pending)return;
  state.lastResponse=data;render();
 }catch(e){if(req===state.pending)showError(e);}
}
function render(){
 const output=$("chart"),data=state.lastResponse;
 output.replaceChildren();$("legend").replaceChildren();$("record-count").textContent="";
 if(!data?.rows?.length){
  output.append(el("div","chart-empty",data?
   "No observations match this selection.":"Choose an indicator and geography to explore the warehouse."));
  return;
 }
 $("record-count").textContent=fmt(data.returned)+" observations"+(data.truncated?" · truncated":"");
 const warnings=[...(data.chart_warnings||[])];
 if(data.layer==="source_native")warnings.unshift("Source-native exploratory values; original units and definitions need verification.");
 warnings.push("Data are not statistically harmonized across providers.");
 notice([...new Set(warnings)].join(" "));
 if(state.chartType==="table"){renderTable(output,data.rows);return;}
 if(!data.can_plot){
  output.append(el("div","chart-empty","This selection needs review before charting. Use Table view to inspect the source records."));
  return;
 }
 renderSvg(output,data.rows,state.chartType);
}
function renderTable(host,rows){
 const wrap=el("div","table-wrap"),table=el("table","data-table"),thead=el("thead"),headrow=el("tr");
 const cols=["Geography","Period","Series","Value","Unit","Provider","Status"];
 for(const c of cols)headrow.append(el("th",c==="Value"?"numeric":"",c));
 thead.append(headrow);table.append(thead);
 const body=el("tbody");
 for(const r of rows){
  const tr=el("tr");
  const vals=[r.geography,r.period,r.series,r.value==null?"Not numeric":short(r.value),r.unit,r.provider,r.source_status];
  vals.forEach((v,i)=>tr.append(el("td",i===3?"numeric":"",String(v??""))));
  body.append(tr);
 }
 table.append(body);wrap.append(table);host.append(wrap);
}
const svgNS="http://www.w3.org/2000/svg";
function svg(tag,attrs={},label){
 const item=document.createElementNS(svgNS,tag);
 for(const [name,value] of Object.entries(attrs))item.setAttribute(name,String(value));
 if(label!==undefined)item.textContent=label;
 return item;
}
function svgTitle(target,label){target.append(svg("title",{},label));return target;}
function formatAxis(n){
 const a=Math.abs(n);
 if(a>=1e9)return (n/1e9).toFixed(1)+"B";
 if(a>=1e6)return (n/1e6).toFixed(1)+"M";
 if(a>=1e3)return (n/1e3).toFixed(1)+"k";
 if(a<.01&&a!==0)return n.toExponential(1);
 return Number(n.toFixed(a<10?2:1)).toLocaleString("en-US");
}
function renderSvg(host,rows,type){
 const groups=new Map();
 for(const r of rows){
  if(r.year==null||r.value==null)continue;
  const geo=r.geography||"Unknown";
  if(!groups.has(geo))groups.set(geo,[]);
  groups.get(geo).push({year:Number(r.year),value:Number(r.value),period:r.period});
 }
 const names=[...groups.keys()].sort();
 if(!names.length){host.append(el("div","chart-empty","No numeric observations to plot."));return;}
 const vals=[...groups.values()].flat(),years=[...new Set(vals.map(x=>x.year))].sort((a,b)=>a-b);
 if(type==="heatmap"&&years.length>160){
  host.append(el("div","chart-empty","Narrow the year range to 160 years for a heatmap."));return;
 }
 if(type==="bar"&&vals.length>300){
  host.append(el("div","chart-empty","This bar chart has over 300 points. Narrow the range or select Line."));return;
 }
 const width=960,height=390,left=80,right=25,top=18,bottom=55,iw=width-left-right,ih=height-top-bottom;
 const minYear=Math.min(...years),maxYear=Math.max(...years),yearRange=Math.max(1,maxYear-minYear);
 const rawMin=Math.min(...vals.map(x=>x.value)),rawMax=Math.max(...vals.map(x=>x.value));
 let minValue=type==="bar"?Math.min(0,rawMin):rawMin;
 let maxValue=type==="bar"?Math.max(0,rawMax):rawMax;
 if(minValue===maxValue){minValue-=Math.max(1,Math.abs(minValue)*.1);maxValue+=Math.max(1,Math.abs(maxValue)*.1);}
 const padding=(maxValue-minValue)*.08;minValue-=padding;maxValue+=padding;
 const posX=y=>left+(y-minYear)/yearRange*iw;
 const posY=v=>top+ih-(v-minValue)/(maxValue-minValue)*ih;
 const root=svg("svg",{viewBox:"0 0 "+width+" "+height,role:"img","aria-label":type+" chart of "+state.lastResponse.series,class:"chart-svg"});
 root.append(svg("rect",{x:left,y:top,width:iw,height:ih,class:"plot-bg"}));
 if(type!=="heatmap"){
  for(let i=0;i<=5;i++){
   const v=minValue+(maxValue-minValue)*i/5,y=posY(v);
   root.append(svg("line",{class:"gridline",x1:left,y1:y,x2:width-right,y2:y}));
   root.append(svg("text",{class:"axis-text","text-anchor":"end",x:left-10,y:y+4},formatAxis(v)));
  }
 }else{
  for(let i=0;i<names.length;i++)root.append(svg("text",{class:"axis-text",x:16,y:top+ih*(i+.5)/names.length},names[i]));
 }
 for(let i=0;i<=6;i++){
  const year=minYear+(maxYear-minYear)*i/6;
  root.append(svg("text",{class:"axis-text","text-anchor":"middle",x:posX(year),y:height-23},String(Math.round(year))));
 }
 if(type==="heatmap"){
  const cw=iw/Math.max(1,years.length),ch=ih/names.length;
  const absMax=Math.max(1,Math.abs(rawMax),Math.abs(rawMin));
  names.forEach((geo,index)=>{
   const color=COLORS[index%COLORS.length];
   const lookup=new Map(groups.get(geo).map(p=>[p.year,p]));
   years.forEach((year,i)=>{
    const p=lookup.get(year);if(!p)return;
    const rect=svg("rect",{x:left+i*cw,y:top+index*ch,width:Math.max(1,cw-.5),
      height:Math.max(1,ch-.5),fill:color,opacity:Math.max(.14,Math.min(.96,Math.abs(p.value)/absMax))});
    root.append(svgTitle(rect,geo+" · "+year+" · "+short(p.value)));
   });
  });
 }else if(type==="bar"){
  const bw=Math.max(2,Math.min(18,iw/(Math.max(1,years.length)*Math.max(1,names.length))*.78));
  names.forEach((geo,index)=>{
   for(const p of groups.get(geo)){
    const baseline=posY(0),y=posY(p.value),center=posX(p.year)+(index-(names.length-1)/2)*bw;
    const rect=svg("rect",{class:"bar",x:center-bw/2,y:Math.min(y,baseline),width:bw,
      height:Math.max(1,Math.abs(y-baseline)),fill:COLORS[index%COLORS.length]});
    root.append(svgTitle(rect,geo+" · "+p.year+" · "+short(p.value)));
   }
  });
 }else{
  names.forEach((geo,index)=>{
   const points=groups.get(geo).slice().sort((a,b)=>a.year-b.year);
   const color=COLORS[index%COLORS.length];
   if(type==="line"&&points.length>1){
    root.append(svg("polyline",{class:"line",stroke:color,
      points:points.map(p=>posX(p.year).toFixed(2)+","+posY(p.value).toFixed(2)).join(" ")}));
   }
   const dots=type==="scatter"||points.length<=180?points:points.filter((p,i)=>i%Math.ceil(points.length/180)===0);
   for(const p of dots){
    root.append(svgTitle(svg("circle",{class:"dot",cx:posX(p.year),cy:posY(p.value),
      r:type==="scatter"?4.5:3.2,fill:color}),geo+" · "+p.period+" · "+short(p.value)));
   }
  });
 }
 root.append(svg("text",{class:"axis-label","text-anchor":"middle",x:width/2,y:height-3},"Year"));
 host.append(root);
 const legend=$("legend");
 names.forEach((geo,index)=>{
  const label=el("span"),dot=el("i");dot.style.background=COLORS[index%COLORS.length];
  label.append(dot,document.createTextNode(geo));legend.append(label);
 });
}
function chooseChart(type){
 state.chartType=type;
 document.querySelectorAll(".chart-type").forEach(x=>x.classList.toggle("active",x.dataset.chart===type));
 render();
}
function download(){
 const f=currentFamily(),series=$("series").value;
 if(!f||!series)return notice("Choose an indicator to export.");
 const args={family:f.id,layer:f.layer,series,geos:[...state.selected].join(","),
  start:$("year-start").value,end:$("year-end").value};
 const url=new URL("/api/export",location.origin);
 Object.entries(args).forEach(([k,v])=>url.searchParams.set(k,v));
 window.location.assign(url.toString());
}
function attach(){
 $("nav-explore").addEventListener("click",()=>mode("explore"));
 $("nav-catalog").addEventListener("click",()=>mode("catalog"));
 $("family").addEventListener("change",loadFamily);
 $("series").addEventListener("change",loadGeographies);
 let debounce;
 $("search-indicator").addEventListener("input",()=>{
  clearTimeout(debounce);debounce=setTimeout(()=>loadSeries($("search-indicator").value.trim()),330);
 });
 $("geo-search").addEventListener("input",renderGeographies);
 $("clear-geo").addEventListener("click",()=>{
  state.selected.clear();renderGeographies();notice("Choose at least one geography.");
 });
 $("apply").addEventListener("click",apply);
 $("csv-export").addEventListener("click",download);
 document.querySelectorAll(".chart-type").forEach(x=>x.addEventListener("click",()=>chooseChart(x.dataset.chart)));
}
async function init(){
 attach();
 try{
  [state.overview,state.families]=await Promise.all([api("/api/overview"),api("/api/families")]);
  populateOverview();fillFamilies();drawCatalog();mode("explore");await loadFamily();
 }catch(e){showError(e);$("source-caption").textContent="Could not access the selected Atlas snapshot.";}
}
init();
