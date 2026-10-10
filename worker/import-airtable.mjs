// Import sécurisé direct dans Airtable. Aucun secret livré au navigateur.
// Cloudflare secrets requis : AIRTABLE_TOKEN et UPLOAD_KEY.
// Photos JPEG <= 4.5 Mo. Statut Dicté jusqu'à validation Discogs.
const BASE="apporVr2Na6l5HGh2",TABLE="tblcqsKKwNW28bMSE";
const CAPTURE_HTML="<!doctype html><html lang=\"fr\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Capture privée</title><h1>Inventaire de vinyles</h1><p>Une photo par pochette. Les photos sont importées dans l'ordre choisi.</p><label>Code personnel <input id=\"key\" type=\"password\" autocomplete=\"off\"></label><label>Bac <input id=\"bac\" value=\"B-08\"></label><label>Total du bac <input id=\"total\" type=\"number\"></label><label>Premier rang <input id=\"start\" type=\"number\" value=\"1\"></label><label>Photos <input id=\"files\" type=\"file\" multiple accept=\"image/*\"></label><button id=\"send\">Importer</button><pre id=\"status\"></pre><script>\nconst e=id=>document.getElementById(id);\ne('send').onclick=async()=>{const fs=Array.from(e('files').files),bac=e('bac').value.trim().toUpperCase(),total=Number(e('total').value),start=Number(e('start').value),key=e('key').value;if(!key||!/^B-\\d{1,5}$/.test(bac)||!Number.isInteger(total)||!Number.isInteger(start)||!fs.length||start<1||start+fs.length-1>total){e('status').textContent='Vérifie les champs';return}e('send').disabled=true;let ok=0;try{for(let i=0;i<fs.length;i++){e('status').textContent='Importation '+(i+1)+' / '+fs.length;const b=await createImageBitmap(fs[i]),c=document.createElement('canvas'),scale=Math.min(1,1600/Math.max(b.width,b.height));c.width=Math.round(b.width*scale);c.height=Math.round(b.height*scale);c.getContext('2d').drawImage(b,0,0,c.width,c.height);b.close();const blob=await new Promise(r=>c.toBlob(r,'image/jpeg',.76));if(!blob||blob.size>4500000)throw Error('Photo trop volumineuse');const a=new Uint8Array(await blob.arrayBuffer());let s='';for(let j=0;j<a.length;j+=8192)s+=String.fromCharCode(...a.slice(j,j+8192));const resp=await fetch('/import',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer '+key},body:JSON.stringify({bac,total,ordre:start+i,image:btoa(s),mime:'image/jpeg'})});const result=await resp.json();if(!resp.ok)throw Error('Rang '+(start+i)+' : '+result.error);ok++}e('status').textContent=ok+' photo(s) enregistrée(s)'}catch(err){e('status').textContent=ok+' photo(s) importée(s). Arrêt : '+err.message}finally{e('send').disabled=false}};\n</script></html>";
const H={"content-type":"application/json","cache-control":"no-store","x-content-type-options":"nosniff"};
const response=(x,s=200)=>new Response(JSON.stringify(x),{status:s,headers:H});
const api=`https://api.airtable.com/v0/${BASE}/${TABLE}`;
async function call(url,token,method="GET",body) {
 const resp=await fetch(url,{method,headers:{"authorization":"Bearer "+token,"content-type":"application/json"},body:body?JSON.stringify(body):undefined});
 const value=await resp.json().catch(()=>({}));
 if(!resp.ok)throw Error("Airtable HTTP "+resp.status);
 return value;
}
export default {async fetch(req,env) {
 const path=new URL(req.url).pathname;
 if(req.method==="GET"&&path==="/capture")return new Response(CAPTURE_HTML,{headers:{"content-type":"text/html; charset=utf-8","cache-control":"no-store","x-frame-options":"DENY","referrer-policy":"no-referrer"}});
 if(path==="/health")return response({configured:!!(env.UPLOAD_KEY&&env.AIRTABLE_TOKEN)});
 if(!env.UPLOAD_KEY||!env.AIRTABLE_TOKEN)return response({error:"Configuration absente"},503);
 const auth=req.headers.get("authorization")||"";
 if(auth.length!==env.UPLOAD_KEY.length+7||auth!=="Bearer "+env.UPLOAD_KEY)return response({error:"Non autorisé"},401);
 if(req.method!=="POST"||path!=="/import")return response({error:"Route inconnue"},404);
 const length=Number(req.headers.get("content-length")||0);
 if(length>6500000)return response({error:"Image trop volumineuse"},413);
 let p;try{p=await req.json()}catch{return response({error:"JSON invalide"},400)}
 const {bac,ordre,total,image,mime}=p||{};
 if(typeof bac!=="string"||!/^B-\d{1,5}$/.test(bac)||!Number.isInteger(ordre)||ordre<1||ordre>200||
 !Number.isInteger(total)||total<ordre||total>200||!["image/jpeg","image/png","image/webp"].includes(mime)||
 typeof image!=="string"||image.length>6200000||!/^[A-Za-z0-9+/]*={0,2}$/.test(image))return response({error:"Photo ou position invalide"},400);
 const formula=`AND({Bac}='${bac}',{Ordre dans le bac}=${ordre})`;
 try{
   const lookup=await call(api+"?"+new URLSearchParams({filterByFormula:formula,pageSize:"2"}),env.AIRTABLE_TOKEN);
   if(lookup.records?.length){const existing=lookup.records[0];const images=existing.fields?.["Front Cover"];if(images?.length)return response({status:"existe",record_id:existing.id},409);return response({error:"Fiche déjà présente mais image absente : correction manuelle nécessaire",record_id:existing.id},409);}
   const id=`${bac}-${String(ordre).padStart(3,"0")}`;
   const created=await call(api,env.AIRTABLE_TOKEN,"POST",{fields:{"Bac":bac,"Ordre dans le bac":ordre,"Total du bac":total,"Statut":"Dicté","Transcription source":"Capture photo brute "+id+" — identification en attente"}});
   const upload=await call(`https://content.airtable.com/v0/${BASE}/${created.id}/Front%20Cover/uploadAttachment`,env.AIRTABLE_TOKEN,"POST",{contentType:mime,filename:id+"."+({"image/jpeg":"jpg","image/png":"png","image/webp":"webp"})[mime],file:image});
   const found=upload.fields?.["Front Cover"]||upload.fields?.["fldFVl8hcVGP28FKt"];
   if(!Array.isArray(found)||!found.length)return response({error:"Photo non confirmée",record_id:created.id},502);
   return response({status:"créé",record_id:created.id,position:id},201);
 }catch(e){return response({error:"Échec de l'import; vérifier Airtable avant tout nouvel essai"},502)}
}};
