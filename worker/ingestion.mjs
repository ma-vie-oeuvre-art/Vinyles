/**
 * Ingestion privée de bacs — jamais de clés Airtable dans le navigateur.
 * Secrets: UPLOAD_KEY, AIRTABLE_TOKEN; binding: VINYL_PHOTOS (R2 privé).
 * Accès : requêtes HTTPS Authorization: Bearer <UPLOAD_KEY>.
 * Ne pas activer de route publique avant provisionnement de ces ressources.
 */
const BASE = "apporVr2Na6l5HGh2";
const TABLE = "tblcqsKKwNW28bMSE";
const json = (data, code=200) => new Response(JSON.stringify(data), {status:code,headers:{"content-type":"application/json; charset=utf-8","cache-control":"no-store","x-content-type-options":"nosniff"}});
const allowed = /^B-\d{1,5}$/;
function safeEqual(a,b) {if (a.length!==b.length) return false;let diff=0;for(let i=0;i<a.length;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);return diff===0;}
export default {
 async fetch(request, env) {
  const url = new URL(request.url);
  if(url.pathname==="/health") return json({service:"vinyles-ingestion",ready:Boolean(env.UPLOAD_KEY && env.AIRTABLE_TOKEN && env.VINYL_PHOTOS)});
  // Fail closed. Never allow unauthenticated changes.
  if(!env.UPLOAD_KEY || !env.AIRTABLE_TOKEN || !env.VINYL_PHOTOS) return json({error:"Service non configuré"},503);
  const auth=request.headers.get("authorization")||"";
  if(!safeEqual(auth,"Bearer "+env.UPLOAD_KEY)) return json({error:"Accès refusé"},401);
  if(request.method==="OPTIONS") return json({error:"CORS désactivé: ingestion réservée"},405);
  if(request.method!=="POST" || url.pathname!=="/batches") return json({error:"Route inconnue"},404);
  if(!request.headers.get("content-type")?.includes("multipart/form-data")) return json({error:"Formulaire attendu"},415);
  const form=await request.formData();
  const bac=String(form.get("bac")||"").toUpperCase();
  const count=Number(form.get("nombre"));
  const photos=form.getAll("photos");
  if(!allowed.test(bac)||!Number.isSafeInteger(count)||count<1||count>200||photos.length<1||photos.length>200) return json({error:"Lot incorrect"},400);
  if(photos.some(x=>!(x instanceof File)||!["image/jpeg","image/png","image/webp","image/heic"].includes(x.type)||x.size>12_000_000||x.size<100))return json({error:"Photo non conforme"},400);
  // Required for idempotence; stable across retries of one submitted batch.
  const batchId=String(form.get("batch_id")||"");
  if(!/^[a-zA-Z0-9_-]{12,64}$/.test(batchId)) return json({error:"Identifiant de lot invalide"},400);
  const manifestKey="batches/"+batchId+"/manifest.json";
  const existing=await env.VINYL_PHOTOS.head(manifestKey);
  if(existing) return json({status:"déjà_reçu",batch_id:batchId,count:photos.length});
  const entries=[];
  for(let i=0;i<photos.length;i++){
    const item=photos[i];const ext=({"image/jpeg":"jpg","image/png":"png","image/webp":"webp","image/heic":"heic"})[item.type];
    const key="batches/"+batchId+"/"+String(i+1).padStart(3,"0")+"."+ext;
    await env.VINYL_PHOTOS.put(key,await item.arrayBuffer(),{httpMetadata:{contentType:item.type}});
    entries.push({bac,position:i+1,key,mime:item.type,size:item.size});
  }
  const manifest={batch_id:batchId,bac,expected_count:count,photos:entries,received_at:new Date().toISOString(),status:"capturé"};
  await env.VINYL_PHOTOS.put(manifestKey,JSON.stringify(manifest),{httpMetadata:{contentType:"application/json"}});
  return json({status:"reçu",batch_id:batchId,bac,photos:entries.length},201);
 }
};
