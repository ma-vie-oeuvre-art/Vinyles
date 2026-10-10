// Import sécurisé direct dans Airtable. Aucun secret livré au navigateur.
// Cloudflare secrets requis : AIRTABLE_TOKEN et UPLOAD_KEY.
// Photos JPEG <= 4.5 Mo. Statut Dicté jusqu'à validation Discogs.
const BASE="apporVr2Na6l5HGh2",TABLE="tblcqsKKwNW28bMSE";
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
   if(lookup.records?.length)return response({status:"existe",record_id:lookup.records[0].id},409);
   const id=`${bac}-${String(ordre).padStart(3,"0")}`;
   const created=await call(api,env.AIRTABLE_TOKEN,"POST",{fields:{"Bac":bac,"Ordre dans le bac":ordre,"Total du bac":total,"Statut":"Dicté","Transcription source":"Capture photo brute "+id+" — identification en attente"}});
   const upload=await call(`https://content.airtable.com/v0/${BASE}/${created.id}/Front%20Cover/uploadAttachment`,env.AIRTABLE_TOKEN,"POST",{contentType:mime,filename:id+"."+({"image/jpeg":"jpg","image/png":"png","image/webp":"webp"})[mime],file:image});
   const found=upload.fields?.["Front Cover"]||upload.fields?.["fldFVl8hcVGP28FKt"];
   if(!Array.isArray(found)||!found.length)return response({error:"Photo non confirmée",record_id:created.id},502);
   return response({status:"créé",record_id:created.id,position:id},201);
 }catch(e){return response({error:"Échec de l'import; vérifier Airtable avant tout nouvel essai"},502)}
}};
