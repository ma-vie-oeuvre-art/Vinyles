// MVOA Airtable + Anthropic Proxy - Cloudflare Worker
// v24 — CORRECTIF DE FOND : la formulation STC prime sur l'étiquette courte.
//        Le compilateur comparait les captures au champ « Titre STC » (un surnom :
//        « Pipeline de traitement », « Père exemplaire ») au lieu du champ
//        « Résultat désiré » qui contient le véritable état accompli
//        (« Une relation père-fille profonde, authentique et structurante avec
//        Laurence »). Sans état accompli, le critère de granularité est
//        inapplicable et tout rattachement devient approximatif.
// v23 — trois correctifs décidés le 29 juillet 2026 :
//        (1) LECTURE DE L'ÉTAT COURANT AVANT PROPOSITION (#37) — le compilateur reçoit
//            désormais les Actions ouvertes et les Blocs temporels existants. Il peut
//            signaler un doublon (doublon_de_action_id) au lieu d'en créer un second.
//        (2) CRITÈRE DE GRANULARITÉ DES RD — le RD juste est le plus petit état accompli
//            que l'action sert entièrement. Si aucun RD au bon niveau n'existe, le
//            compilateur PROPOSE un RD secondaire (rd_propose) sous un parent, sans
//            jamais l'écrire : Fritz exige que le désir vienne du vécu (#206).
//        (3) NOYAU MINIMAL #184 — /validate accepte et écrit les champs de contexte
//            (bloc temporel, récurrence, échéance, état/énergie) et retourne
//            noyauMinimalComplet. Une Action n'entre plus sans prise sur le réel.
// v22 — compteurs honnêtes de /decisions/search
// v21 — état déployé au 27 juillet 2026
// v20 — images téléchargées par le Worker et passées en base64
// v19 — traçabilité : méthode de lecture + provenance du titre
// v18 — le titre réel de la ressource remplit Élément capturé quand il est vide (#10)
// v17 — cascade de lecture des liens : direct → YouTube oEmbed → Jina → archive.org
// v16 — compile-v3 : lecture du contenu réel des liens + vision des images
// v15 — compile-v2 : captures par lien/image compilées
// v14 — ajout /validate
// v13 — ajout /compile (Décision #248)
// v12 — implantation contrôlée : Actions seulement

const ALLOWED_ORIGIN = 'https://ma-vie-oeuvre-art.github.io';
const AIRTABLE_API = 'https://api.airtable.com';
const ANTHROPIC_API = 'https://api.anthropic.com';

const MVOA_BASE_ID = 'appKrZTdmGkPsaDF7';
const DECISIONS_TABLE = 'Décisions architecturales';
const ENTREES_BRUTES_TABLE = 'Entrées brutes';
const RD_TABLE = 'Résultats désirés';
const ACTIONS_TABLE = 'Actions';
const BLOCS_TABLE = 'Blocs temporels CDU';

const ALLOWED_CREATE_TABLES = ['Actions'];

// ===== COMPILATION (Décision #248) =====
// v23 : compile-v4 — lecture de l'état courant + critère de granularité des RD.
const COMPILE_PROMPT_VERSION = 'compile-v4';
const COMPILE_MODEL = 'claude-sonnet-4-6';

// Statuts considérés comme fermés : une Action fermée ne peut pas être un doublon actif.
const STATUTS_FERMES = ['complété', 'complétée', 'terminé', 'terminée', 'annulé', 'annulée', 'abandonné', 'abandonnée'];

function estActionOuverte(record) {
  const statut = String(record.fields['Statut'] || '').toLowerCase().trim();
  if (!statut) return true;
  return !STATUTS_FERMES.includes(statut);
}

// Le nom du champ primaire peut varier ; on prend la première valeur texte utile.
function premierTexte(fields, nomsProbables) {
  for (const nom of nomsProbables) {
    if (fields[nom] && typeof fields[nom] === 'string' && fields[nom].trim()) return fields[nom].trim();
  }
  for (const valeur of Object.values(fields)) {
    if (typeof valeur === 'string' && valeur.trim()) return valeur.trim();
  }
  return '';
}

// ============================================================================
// COUCHE DE LECTURE — v25
// Va chercher le contenu réel d'une ressource pour que la Clarification AI
// analyse la pièce elle-même, pas seulement son adresse.
//
// Décisions portées ici :
//   V2-#35  une source fermée n'arrête pas la chaîne : quand la page est
//           illisible et qu'une clé existe, on cherche l'équivalent ouvert.
//           Jamais silencieux : l'URL d'origine reste, la substitution est
//           déclarée DÉDUITE avec la clé qui a servi.
//   V2-#36  le pipeline doit disposer d'une étape de transcription entre
//           « j'ai une URL vidéo » et « j'ai du texte à compiler ».
//
// CASCADE, sept étapes, arrêt au premier succès :
//   1) lecture directe de la page
//   2) métadonnées standard : oEmbed déclaré, oEmbed connu, noembed
//   3) transcription des sous-titres (YouTube)
//   4) lecteur Jina (pages rendues en JavaScript, paywall léger)
//   5) copie archivée (archive.org)
//   6) métadonnées servies aux robots sociaux (og: derrière un mur de session)
//   7) résolution vers une source ouverte, puis relecture de celle-ci
//
// Chaque tentative est journalisée. Un échec total dit ce qui a été essayé,
// jamais un simple « statut 400 ».
// ============================================================================
async function lireContenuLien(url, env) {
  const journal = [];
  const cible = normaliserUrl(url);
  if (cible !== url) journal.push(`URL normalisée : ${cible}`);

  const resultat = await lireCascade(cible, env, journal, true);
  const bloc = '\n\nJOURNAL DE LECTURE :\n- ' + journal.join('\n- ');

  if (resultat) {
    // Le marqueur « (source : X) » doit demeurer le tout dernier élément de la
    // chaîne : runCompilation en extrait la méthode de lecture avec une ancre de
    // fin. On détache le marqueur, on insère le journal, puis on le remet.
    const m = resultat.match(/\n?\(source : [^)]+\)\s*$/);
    if (m) {
      return resultat.slice(0, m.index) + bloc + '\n' + m[0].trim();
    }
    return resultat + bloc;
  }

  return '(contenu inaccessible par toutes les méthodes)'
    + bloc
    + '\n\nAucune affirmation sur le contenu de cette ressource n\'est possible.';
}

// autoriserResolution est faux quand on relit déjà une source substituée :
// on ne résout pas une résolution, pour éviter toute dérive en chaîne.
async function lireCascade(url, env, journal, autoriserResolution) {
  const indices = { titre: '', auteur: '' };

  // ---- 1) lecture directe ----
  const direct = await lireDirect(url, 7000);
  if (direct.titre) indices.titre = direct.titre;
  if (direct.auteur) indices.auteur = direct.auteur;
  journal.push(`lecture directe : ${direct.ok ? 'réussie' : 'échec — ' + direct.texte}`);
  if (direct.ok) return direct.texte + '\n(source : lecture directe)';

  // ---- 2) métadonnées standard (oEmbed) ----
  const meta = await lireOEmbed(url);
  if (meta) {
    if (!indices.titre && meta.titre) indices.titre = meta.titre;
    if (!indices.auteur && meta.auteur) indices.auteur = meta.auteur;
    journal.push(`oEmbed : réussi via ${meta.fournisseur}`);
    if (meta.suffisant) return meta.texte + `\n(source : oEmbed ${meta.fournisseur})`;
  } else {
    journal.push('oEmbed : aucun point de terminaison exploitable');
  }

  // ---- 3) transcription des sous-titres (V2-#36) ----
  if (/youtube\.com|youtu\.be/i.test(url)) {
    const transcription = await lireTranscriptionYouTube(url);
    if (transcription) {
      journal.push('transcription : sous-titres récupérés');
      const entete = [
        indices.titre ? 'TITRE DE LA VIDÉO : ' + indices.titre : '',
        indices.auteur ? 'AUTEUR : ' + indices.auteur : '',
      ].filter(Boolean).join('\n');
      return (entete ? entete + '\n\n' : '')
        + 'TRANSCRIPTION DES SOUS-TITRES :\n' + transcription
        + '\n(source : sous-titres de la vidéo — texte de la pièce, non une inférence)';
    }
    journal.push('transcription : aucun sous-titre accessible');
  }

  // ---- 4) lecteur Jina ----
  const jina = await lireViaJina(url);
  journal.push(`lecteur Jina : ${jina ? 'réussi' : 'échec'}`);
  if (jina) return jina + '\n(source : lecteur Jina)';

  // ---- 5) copie archivée ----
  const archive = await lireViaArchive(url);
  journal.push(`archive.org : ${archive ? 'copie trouvée' : 'aucune copie'}`);
  if (archive) return archive + '\n(source : copie archivée archive.org)';

  // ---- 6) métadonnées servies aux robots sociaux ----
  const social = await lireCommeRobotSocial(url);
  if (social) {
    if (!indices.titre && social.titre) indices.titre = social.titre;
    if (!indices.auteur && social.auteur) indices.auteur = social.auteur;
    journal.push('métadonnées sociales : balises og: obtenues');
    if (social.suffisant) return social.texte + '\n(source : métadonnées og: de la page)';
  } else {
    journal.push('métadonnées sociales : rien');
  }

  // ---- 7) résolution vers une source ouverte (V2-#35) ----
  if (!autoriserResolution) {
    journal.push('résolution : non tentée (on relisait déjà une source substituée)');
    return null;
  }
  if (!indices.titre) {
    journal.push('résolution : non tentée — aucune clé exploitable, conformément à V2-#35');
    return null;
  }

  const resolution = await resoudreSourceOuverte(indices, url, env);
  if (!resolution || !resolution.url) {
    journal.push(`résolution : aucun équivalent ouvert identifié pour la clé « ${indices.titre} »`);
    return null;
  }

  journal.push(`résolution : équivalent ouvert proposé — ${resolution.url}`);
  const relu = await lireCascade(resolution.url, env, journal, false);
  if (!relu) {
    journal.push('résolution : équivalent trouvé mais lui-même illisible');
    return null;
  }

  return 'DÉDUIT — SOURCE SUBSTITUÉE (V2-#35).'
    + `\nURL d'origine, conservée : ${url}`
    + `\nSource ouverte retenue : ${resolution.url}`
    + `\nClé ayant servi : « ${indices.titre} »${indices.auteur ? ' / ' + indices.auteur : ''}`
    + (resolution.justification ? `\nJustification : ${resolution.justification}` : '')
    + '\nL\'identité entre la pièce d\'origine et celle-ci est une inférence, non un fait vérifié.'
    + '\n\n' + relu;
}

// Enlève les paramètres de suivi et déplie les formes courtes connues.
function normaliserUrl(url) {
  try {
    const u = new URL(url.trim());
    const parasites = ['fbclid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_term',
                       'utm_content', 'igshid', 'si', 'mibextid', 'rdid'];
    for (const p of parasites) u.searchParams.delete(p);
    return u.toString();
  } catch (err) {
    return url.trim();
  }
}

async function lireDirect(url, timeoutMs) {
  try {
    const response = await fetchAvecTimeout(url, timeoutMs, {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml',
      'Accept-Language': 'fr-CA,fr;q=0.9,en;q=0.7',
    });
    const typeContenu = response.headers.get('content-type') || '';
    const estTexte = typeContenu.includes('text/') || typeContenu.includes('xml')
      || typeContenu.includes('json') || typeContenu === '';

    // Un statut d'erreur ne signifie pas un corps vide : beaucoup de plateformes
    // répondent 400 ou 403 tout en servant les balises og:. On lit quand même.
    if (!estTexte) {
      return { ok: false, texte: `contenu non textuel : ${typeContenu.split(';')[0]}`, titre: '', auteur: '' };
    }

    const html = (await response.text()).substring(0, 400000);
    const titre = extraireMeta(html, ['og:title', 'twitter:title']) || extraireBaliseTitre(html);
    const auteur = extraireMeta(html, ['author', 'og:site_name', 'twitter:creator']);
    const texte = extraireTexteHtml(html);
    const suffisant = response.ok && texte.length > 250;

    let diagnostic = '';
    if (!response.ok) diagnostic = `statut ${response.status}`;
    else if (texte.length <= 250) diagnostic = 'page presque vide (probablement rendue en JavaScript)';

    return {
      ok: suffisant,
      texte: suffisant ? texte : diagnostic,
      titre: titre || '',
      auteur: auteur || '',
    };
  } catch (err) {
    return { ok: false, texte: `lecture impossible : ${err.message}`, titre: '', auteur: '' };
  }
}

// oEmbed est un standard : la plupart des plateformes vidéo et sociales
// l'exposent, et il répond sans session authentifiée.
const POINTS_OEMBED = [
  { motif: /youtube\.com|youtu\.be/i,   nom: 'YouTube',     url: 'https://www.youtube.com/oembed?format=json&url=' },
  { motif: /vimeo\.com/i,               nom: 'Vimeo',       url: 'https://vimeo.com/api/oembed.json?url=' },
  { motif: /tiktok\.com/i,              nom: 'TikTok',      url: 'https://www.tiktok.com/oembed?url=' },
  { motif: /soundcloud\.com/i,          nom: 'SoundCloud',  url: 'https://soundcloud.com/oembed?format=json&url=' },
  { motif: /open\.spotify\.com/i,       nom: 'Spotify',     url: 'https://open.spotify.com/oembed?url=' },
  { motif: /twitter\.com|\bx\.com/i,    nom: 'X',           url: 'https://publish.twitter.com/oembed?url=' },
  { motif: /reddit\.com/i,              nom: 'Reddit',      url: 'https://www.reddit.com/oembed?url=' },
];

async function lireOEmbed(url) {
  const candidats = [];
  for (const point of POINTS_OEMBED) {
    if (point.motif.test(url)) candidats.push({ nom: point.nom, url: point.url + encodeURIComponent(url) });
  }
  // noembed couvre des dizaines de plateformes supplémentaires sans clé.
  candidats.push({ nom: 'noembed', url: 'https://noembed.com/embed?url=' + encodeURIComponent(url) });

  for (const candidat of candidats) {
    try {
      const response = await fetchAvecTimeout(candidat.url, 6000, { 'Accept': 'application/json' });
      if (!response.ok) continue;
      const data = await response.json();
      if (!data || data.error) continue;

      const titre = data.title || '';
      const auteur = data.author_name || data.provider_name || '';
      const description = data.description || '';
      if (!titre && !description) continue;

      const parties = [];
      if (titre) parties.push('TITRE DE LA PAGE : ' + titre);
      if (auteur) parties.push('AUTEUR OU CHAÎNE : ' + auteur);
      if (description) parties.push('DESCRIPTION : ' + description);

      return {
        fournisseur: candidat.nom,
        titre,
        auteur,
        texte: parties.join('\n'),
        // Un titre seul ne suffit pas à parler du contenu : on continue la cascade.
        suffisant: description.length > 250,
      };
    } catch (err) {
      continue;
    }
  }
  return null;
}

// V2-#36 — la transcription : là où le texte de la pièce existe réellement.
async function lireTranscriptionYouTube(url) {
  try {
    const id = extraireIdYouTube(url);
    if (!id) return null;

    const page = await fetchAvecTimeout('https://www.youtube.com/watch?v=' + id, 9000, {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept-Language': 'en-US,en;q=0.9,fr;q=0.7',
    });
    if (!page.ok) return null;
    const html = await page.text();

    const bloc = html.match(/"captionTracks":\s*(\[[^\]]*\])/);
    if (!bloc) return null;
    let pistes;
    try {
      pistes = JSON.parse(bloc[1].replace(/\\u0026/g, '&'));
    } catch (err) {
      return null;
    }
    if (!Array.isArray(pistes) || pistes.length === 0) return null;

    // Priorité : français, puis anglais, puis la première disponible.
    const choisir = (code) => pistes.find(p => (p.languageCode || '').toLowerCase().startsWith(code));
    const piste = choisir('fr') || choisir('en') || pistes[0];
    if (!piste || !piste.baseUrl) return null;

    const soustitres = await fetchAvecTimeout(piste.baseUrl.replace(/\\u0026/g, '&'), 9000, {});
    if (!soustitres.ok) return null;
    const xml = await soustitres.text();

    const morceaux = [];
    const motif = /<text[^>]*>([\s\S]*?)<\/text>/g;
    let m;
    while ((m = motif.exec(xml)) !== null) {
      const segment = decoderEntites(m[1].replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
      if (segment) morceaux.push(segment);
    }
    if (morceaux.length === 0) return null;

    const texte = morceaux.join(' ').substring(0, 12000);
    const langue = (piste.languageCode || '?').toLowerCase();
    return `[langue des sous-titres : ${langue}]\n${texte}`;
  } catch (err) {
    return null;
  }
}

function extraireIdYouTube(url) {
  const m = url.match(/(?:v=|\/shorts\/|\/embed\/|youtu\.be\/|\/live\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

async function lireViaJina(url) {
  try {
    const response = await fetchAvecTimeout('https://r.jina.ai/' + url, 10000, {
      'Accept': 'text/plain',
    });
    if (!response.ok) return null;
    const brut = await response.text();
    const mTitre = brut.match(/^Title:\s*(.+)$/m);
    const texte = brut.replace(/\s+/g, ' ').trim().substring(0, 2500);
    if (texte.length <= 150) return null;
    return (mTitre ? 'TITRE DE LA PAGE : ' + mTitre[1].trim() + '\n' : '') + 'EXTRAIT DU CONTENU : ' + texte;
  } catch (err) {
    return null;
  }
}

async function lireViaArchive(url) {
  try {
    const dispoUrl = 'https://archive.org/wayback/available?url=' + encodeURIComponent(url);
    const response = await fetchAvecTimeout(dispoUrl, 6000, {});
    if (!response.ok) return null;
    const data = await response.json();
    const snapshot = data && data.archived_snapshots && data.archived_snapshots.closest;
    if (!snapshot || !snapshot.available || !snapshot.url) return null;
    const copie = await lireDirect(snapshot.url.replace(/^http:/, 'https:'), 8000);
    return copie.ok ? copie.texte : null;
  } catch (err) {
    return null;
  }
}

// Certaines plateformes refusent un navigateur mais servent les balises og:
// aux robots des réseaux sociaux, qui doivent bien afficher un aperçu.
async function lireCommeRobotSocial(url) {
  const agents = [
    'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
    'Twitterbot/1.0',
  ];
  for (const agent of agents) {
    try {
      const response = await fetchAvecTimeout(url, 6000, {
        'User-Agent': agent,
        'Accept': 'text/html,application/xhtml+xml',
      });
      const typeContenu = response.headers.get('content-type') || '';
      if (!typeContenu.includes('text/') && typeContenu !== '') continue;
      const html = (await response.text()).substring(0, 200000);

      const titre = extraireMeta(html, ['og:title', 'twitter:title']) || extraireBaliseTitre(html);
      const description = extraireMeta(html, ['og:description', 'twitter:description', 'description']);
      const auteur = extraireMeta(html, ['og:site_name', 'author']);
      if (!titre && !description) continue;

      const parties = [];
      if (titre) parties.push('TITRE DE LA PAGE : ' + titre);
      if (auteur) parties.push('AUTEUR OU SITE : ' + auteur);
      if (description) parties.push('DESCRIPTION : ' + description);

      return {
        titre: titre || '',
        auteur: auteur || '',
        texte: parties.join('\n'),
        suffisant: (description || '').length > 250,
      };
    } catch (err) {
      continue;
    }
  }
  return null;
}

// V2-#35 — chercher l'équivalent ouvert. Deux tentatives ordonnées :
// le titre exact d'abord, les surfaces propres de l'auteur ensuite.
// La recherche est déléguée au modèle muni de l'outil de recherche web.
async function resoudreSourceOuverte(indices, urlOrigine, env) {
  if (!env || !env.ANTHROPIC_API_KEY) return null;
  try {
    const consigne = [
      'Une ressource est illisible parce que la plateforme exige une session authentifiée.',
      `URL d'origine : ${urlOrigine}`,
      `Titre affiché : ${indices.titre}`,
      indices.auteur ? `Auteur ou chaîne : ${indices.auteur}` : '',
      '',
      'Tâche : trouver la MÊME pièce sur une source ouverte et lisible sans connexion.',
      'Procède dans cet ordre :',
      '1. recherche du titre exact ;',
      "2. si rien, recherche sur les surfaces propres de l'auteur (sa chaîne, son site, ses canaux d'extraits).",
      '',
      "N'invente jamais une URL. Ne propose une correspondance que si le titre ou le contenu",
      "identifient la même pièce sans ambiguïté. Un épisode voisin du même auteur n'est PAS une",
      'correspondance : dans le doute, réponds trouve=false.',
      '',
      'Réponds UNIQUEMENT par un objet JSON, sans préambule et sans balises Markdown :',
      '{"trouve": true|false, "url": "...", "justification": "une phrase"}',
    ].filter(Boolean).join('\n');

    const response = await fetchAvecTimeout('https://api.anthropic.com/v1/messages', 25000, {
      'Content-Type': 'application/json',
      'x-api-key': env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
    }, {
      method: 'POST',
      body: JSON.stringify({
        model: 'claude-sonnet-4-6',
        max_tokens: 1000,
        messages: [{ role: 'user', content: consigne }],
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
      }),
    });
    if (!response.ok) return null;
    const data = await response.json();

    const texte = (data.content || [])
      .filter(bloc => bloc.type === 'text')
      .map(bloc => bloc.text)
      .join('\n');
    const nettoye = texte.replace(/```json|```/g, '').trim();
    const debut = nettoye.indexOf('{');
    const fin = nettoye.lastIndexOf('}');
    if (debut === -1 || fin === -1) return null;

    const verdict = JSON.parse(nettoye.substring(debut, fin + 1));
    if (!verdict.trouve || !verdict.url) return null;
    if (!/^https?:\/\//i.test(verdict.url)) return null;
    return { url: verdict.url, justification: verdict.justification || '' };
  } catch (err) {
    return null;
  }
}

function extraireMeta(html, proprietes) {
  for (const propriete of proprietes) {
    const echappee = propriete.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const avant = new RegExp(
      `<meta[^>]+(?:property|name)=["']${echappee}["'][^>]*content=["']([^"']*)["']`, 'i');
    const apres = new RegExp(
      `<meta[^>]+content=["']([^"']*)["'][^>]*(?:property|name)=["']${echappee}["']`, 'i');
    const trouve = (html.match(avant) || [])[1] || (html.match(apres) || [])[1];
    if (trouve && trouve.trim()) return decoderEntites(trouve.trim());
  }
  return '';
}

function extraireBaliseTitre(html) {
  const m = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return m ? decoderEntites(m[1].trim()) : '';
}

function fetchAvecTimeout(url, timeoutMs, headers, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, Object.assign(
    { signal: controller.signal, redirect: 'follow', headers },
    options || {}
  )).finally(() => clearTimeout(timer));
}

function extraireTexteHtml(html) {
  const sansScripts = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ');

  const titre = decoderEntites(((sansScripts.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || '').trim());
  const brutDesc = (sansScripts.match(/<meta[^>]+(?:name=["']description["']|property=["']og:description["'])[^>]*content=["']([^"']*)["']/i) || [])[1]
    || (sansScripts.match(/<meta[^>]+content=["']([^"']*)["'][^>]*(?:name=["']description["']|property=["']og:description["'])/i) || [])[1]
    || '';
  const metaDesc = decoderEntites(brutDesc.trim());
  const corps = decoderEntites(
    sansScripts.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
  ).substring(0, 2500);

  const parties = [];
  if (titre) parties.push('TITRE DE LA PAGE : ' + titre);
  if (metaDesc) parties.push('DESCRIPTION : ' + metaDesc);
  if (corps) parties.push('EXTRAIT DU CONTENU : ' + corps);
  return parties.join('\n');
}

function decoderEntites(texte) {
  return String(texte)
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;|&apos;|&rsquo;|&#8217;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&eacute;/g, 'é').replace(/&egrave;/g, 'è').replace(/&agrave;/g, 'à')
    .replace(/&ecirc;/g, 'ê').replace(/&ccedil;/g, 'ç').replace(/&ucirc;/g, 'û')
    .replace(/&#\d+;/g, ' ');
}

const MVOA_TOOLS = [
  {
    name: 'search_decisions',
    description: "Recherche dans les décisions architecturales MVOA par mots-clés.",
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Mots-clés de recherche' },
        limit: { type: 'number', description: 'Nombre max de résultats (défaut 8, max 20)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'search_records',
    description: "Lit les records d'une table MVOA avec filtre Airtable optionnel.",
    input_schema: {
      type: 'object',
      properties: {
        table: { type: 'string', description: 'Nom exact de la table' },
        filter: { type: 'string', description: "Formule Airtable optionnelle" },
        fields: { type: 'string', description: 'Champs à retourner, séparés par virgule' },
        maxRecords: { type: 'number', description: 'Nombre max de records (défaut 50, max 200)' },
      },
      required: ['table'],
    },
  },
  {
    name: 'list_tables',
    description: "Liste toutes les tables de la base MVOA avec leurs champs exacts.",
    input_schema: { type: 'object', properties: {} },
  },
  {
    name: 'propose_action',
    description: "Propose la création d'une nouvelle Action. NE CRÉE RIEN. Applique le test #93 avant utilisation.",
    input_schema: {
      type: 'object',
      properties: {
        fields: { type: 'object', description: "Champs Airtable pour la table Actions." },
        rationale: { type: 'string', description: 'Pourquoi cette action.' },
        executabilityCheck: { type: 'string', description: "Confirmation du test #93." },
      },
      required: ['fields', 'rationale', 'executabilityCheck'],
    },
  },
];

async function executeTool(toolName, toolInput, env) {
  try {
    switch (toolName) {
      case 'search_decisions': {
        const query = toolInput.query || '';
        const limit = Math.min(toolInput.limit || 8, 20);
        const found = await searchDecisionsInternal(query, limit, env);
        const tronque = found.totalMatched > found.results.length;
        return {
          results: found.results,
          returned: found.results.length,
          totalMatched: found.totalMatched,
          corpusSize: found.corpusSize,
          hasMore: tronque,
          disclaimer: tronque
            ? `Résultats tronqués : ${found.results.length} sur ${found.totalMatched} correspondances. L'absence d'une décision dans ces résultats ne prouve pas qu'elle n'existe pas.`
            : `${found.totalMatched} correspondance(s) retournée(s) intégralement, sur ${found.corpusSize} décisions interrogées (statuts Verrouillé et En exploration seulement).`,
        };
      }
      case 'search_records': {
        const table = toolInput.table;
        if (!table) return { error: 'Le paramètre "table" est requis' };
        const filter = toolInput.filter || null;
        const fieldsParam = toolInput.fields || null;
        const maxRecords = Math.min(toolInput.maxRecords || 50, 200);
        const records = await fetchAllRecords(MVOA_BASE_ID, table, filter, env);
        const truncated = records.slice(0, maxRecords);
        const fieldList = fieldsParam ? fieldsParam.split(',').map(f => f.trim()) : null;
        const mapped = truncated.map(r => {
          if (!fieldList) return { id: r.id, fields: r.fields };
          const filtered = {};
          for (const f of fieldList) filtered[f] = r.fields[f];
          return { id: r.id, fields: filtered };
        });
        return { table, totalReturned: mapped.length, hasMore: records.length > maxRecords, records: mapped };
      }
      case 'list_tables': {
        const metaUrl = `${AIRTABLE_API}/v0/meta/bases/${MVOA_BASE_ID}/tables`;
        const response = await fetch(metaUrl, { headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}` } });
        if (!response.ok) {
          const errText = await response.text();
          return { error: `Erreur Airtable Meta API (${response.status}): ${errText}` };
        }
        const data = await response.json();
        const tables = (data.tables || []).map(t => ({
          name: t.name,
          fields: (t.fields || []).map(f => ({ name: f.name, type: f.type })),
        }));
        return { tables };
      }
      default:
        return { error: `Outil inconnu : ${toolName}` };
    }
  } catch (err) {
    return { error: `Erreur lors de l'exécution de ${toolName}: ${err.message}` };
  }
}

function getCurrentDateContext() {
  const now = new Date();
  const longFormatter = new Intl.DateTimeFormat('fr-CA', {
    timeZone: 'America/Montreal',
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
  const isoFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Montreal',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  return {
    dateStr: longFormatter.format(now),
    isoDate: isoFormatter.format(now),
  };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/' || path === '/health') {
      return new Response(JSON.stringify({ status: 'ok', service: 'mvoa-api', version: 'v24' }), {
        headers: { ...corsHeaders(), 'Content-Type': 'application/json' },
      });
    }

    if (path === '/compile' && request.method === 'POST') {
      return handleCompile(request, env, ctx);
    }
    if (path === '/validate' && request.method === 'POST') {
      return handleValidate(request, env);
    }
    if (path === '/decisions/search' && request.method === 'GET') {
      return handleDecisionsSearch(url, env);
    }
    if (path === '/decisions/create' && request.method === 'POST') {
      return handleDecisionsCreate(request, env);
    }
    if (path === '/decisions/link' && request.method === 'POST') {
      return handleDecisionsLink(request, env);
    }
    if (path === '/tables/list' && request.method === 'GET') {
      return handleTablesList(env);
    }
    if (path === '/records/search' && request.method === 'GET') {
      return handleRecordsSearch(url, env);
    }
    if (path === '/records/create' && request.method === 'POST') {
      return handleRecordsCreate(request, env);
    }
    if (path === '/claude/contextualized' && request.method === 'POST') {
      return handleClaudeContextualized(request, env);
    }

    if (path.startsWith('/api/airtable/')) {
      const airtablePath = path.replace('/api/airtable/', '');
      const airtableUrl = `${AIRTABLE_API}/${airtablePath}${url.search}`;
      const airtableResponse = await fetch(airtableUrl, {
        method: request.method,
        headers: {
          'Authorization': `Bearer ${env.AIRTABLE_PAT}`,
          'Content-Type': 'application/json',
        },
        body: request.method !== 'GET' ? await request.text() : undefined,
      });
      const responseBody = await airtableResponse.text();
      return new Response(responseBody, {
        status: airtableResponse.status,
        headers: { ...corsHeaders(), 'Content-Type': 'application/json' },
      });
    }

    if (path.startsWith('/api/anthropic/')) {
      const anthropicPath = path.replace('/api/anthropic/', '');
      const anthropicUrl = `${ANTHROPIC_API}/${anthropicPath}${url.search}`;
      const anthropicResponse = await fetch(anthropicUrl, {
        method: request.method,
        headers: {
          'x-api-key': env.ANTHROPIC_API_KEY,
          'anthropic-version': '2023-06-01',
          'Content-Type': 'application/json',
        },
        body: request.method !== 'GET' ? await request.text() : undefined,
      });
      const responseBody = await anthropicResponse.text();
      return new Response(responseBody, {
        status: anthropicResponse.status,
        headers: { ...corsHeaders(), 'Content-Type': 'application/json' },
      });
    }

    return new Response(JSON.stringify({ error: 'Not found' }), {
      status: 404,
      headers: { ...corsHeaders(), 'Content-Type': 'application/json' },
    });
  },
};

// ============================================================
// /compile — Décision #248, enrichi v23
// La Clarification AI prépare et maintient une proposition
// valide jusqu'à la décision de Luc.
//
// v23 : le compilateur reçoit l'état courant (Actions ouvertes,
// Blocs temporels) et applique le critère de granularité des RD.
// ============================================================
async function handleCompile(request, env, ctx) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }

  const recordId = body.recordId;
  if (!recordId || !/^rec[A-Za-z0-9]{14}$/.test(recordId)) {
    return jsonResponse({ error: 'Paramètre "recordId" requis (format recXXXXXXXXXXXXXX)' }, 400);
  }

  if (env.COMPILE_SECRET && body.secret !== env.COMPILE_SECRET) {
    return jsonResponse({ error: 'Secret invalide' }, 403);
  }

  ctx.waitUntil(runCompilation(recordId, env));

  return jsonResponse({
    success: true,
    status: 'compilation_started',
    recordId,
    promptVersion: COMPILE_PROMPT_VERSION,
  }, 202);
}

async function runCompilation(recordId, env) {
  try {
    const recordUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(ENTREES_BRUTES_TABLE)}/${recordId}`;
    const recordResponse = await fetch(recordUrl, {
      headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}` },
    });
    if (!recordResponse.ok) {
      throw new Error(`Lecture de l'entrée impossible (${recordResponse.status})`);
    }
    const record = await recordResponse.json();
    const elementCapture = record.fields['Élément capturé'] || '';
    const notes = record.fields['Notes'] || '';
    const url = record.fields['URL'] || '';
    const attachments = record.fields['Attachments'] || [];
    const sourceResonance = record.fields['Source résonance'] || [];
    const nomsPieces = attachments.concat(sourceResonance)
      .map(a => a.filename || '')
      .filter(Boolean);

    const rienDuTout = !elementCapture.trim() && !notes.trim() && !url.trim() && nomsPieces.length === 0;
    if (rienDuTout) {
      await writeCompilationError(recordId, 'Entrée vide — aucun texte, lien, note ni pièce jointe.', env);
      return;
    }

    let contenuLien = '';
    if (url.trim()) {
      contenuLien = await lireContenuLien(url.trim(), env);
    }

    const candidatsImages = attachments.concat(sourceResonance)
      .filter(a => a.type && a.type.startsWith('image/') && a.url)
      .slice(0, 2);
    const images = [];
    for (const img of candidatsImages) {
      const bloc = await telechargerImageBase64(img);
      if (bloc) images.push(bloc);
    }

    // ---- ÉTAT COURANT (v23, décision #37) ----
    // Le compilateur ne peut pas éviter un doublon s'il ne sait pas ce qui existe déjà.
    const rdRecords = await fetchAllRecords(
      MVOA_BASE_ID,
      RD_TABLE,
      null,
      env,
      ['Résultat désiré', 'Titre STC']
    );
    // v24 : la formulation STC ('Résultat désiré') prime sur l'étiquette courte
    // ('Titre STC'). Comparer une capture à « Pipeline de traitement » plutôt qu'à
    // « Toute entrée brute transformable en STC complet » rend le critère de
    // granularité inapplicable.
    const rdList = rdRecords.slice(0, 200).map(r => {
      const formulation = String(r.fields['Résultat désiré'] || '').trim();
      const etiquette = String(r.fields['Titre STC'] || '').trim();
      return {
        id: r.id,
        titre: (formulation || etiquette).substring(0, 200),
        etiquette: formulation && etiquette ? etiquette : null,
      };
    }).filter(r => r.titre);
    const rdListText = rdList
      .map(r => `- [${r.id}] ${r.titre}` + (r.etiquette ? ` (raccourci : ${r.etiquette})` : ''))
      .join('\n');

    let actionsList = [];
    let blocsList = [];
    let etatCourantErreur = null;
    try {
      const actionsRecords = await fetchAllRecords(MVOA_BASE_ID, ACTIONS_TABLE, null, env, ['Action', 'Statut']);
      actionsList = actionsRecords
        .filter(estActionOuverte)
        .slice(0, 300)
        .map(r => ({ id: r.id, titre: String(r.fields['Action'] || '').substring(0, 140) }))
        .filter(a => a.titre);

      const blocsRecords = await fetchAllRecords(MVOA_BASE_ID, BLOCS_TABLE, null, env);
      blocsList = blocsRecords
        .map(r => ({ id: r.id, nom: premierTexte(r.fields, ['Bloc', 'Nom', 'Bloc temporel', 'Titre']).substring(0, 100) }))
        .filter(b => b.nom);
    } catch (err) {
      // L'absence d'état courant dégrade la qualité mais ne doit pas faire échouer la compilation.
      etatCourantErreur = err.message;
    }

    const actionsListText = actionsList.length > 0
      ? actionsList.map(a => `- [${a.id}] ${a.titre}`).join('\n')
      : '(aucune Action ouverte lisible)';
    const blocsListText = blocsList.length > 0
      ? blocsList.map(b => `- [${b.id}] ${b.nom}`).join('\n')
      : '(aucun bloc temporel lisible)';

    const { dateStr, isoDate } = getCurrentDateContext();

    const systemPrompt = `Tu es la Clarification AI de MVOA (Ma Vie Œuvre d'Art), le système de gestion de vie de Luc, fondé sur le Structural Tension Charting de Robert Fritz.

Date actuelle : ${dateStr} (${isoDate}), fuseau America/Montreal.

TA TÂCHE : analyser une Entrée brute que Luc vient de capturer, et préparer une proposition complète pour qu'à l'ouverture, Luc n'ait qu'à juger — jamais à construire.

CADRE FRITZ :
- Action = geste concret exécutable, orienté vers un Résultat désiré.
- Résultat désiré (RD) = état accompli, formulé SANS verbe d'action orienté processus (Décision #166).
- Réalité actuelle (RA) = constat factuel du présent, sans jugement.
- Observation = résonance à conserver telle quelle, sans transformation (matière vivante).
- Incertain = l'entrée est trop ambiguë pour trancher avec confiance.

CRITÈRE DE GRANULARITÉ DES RD — RÈGLE CENTRALE :
Le RD juste est LE PLUS PETIT ÉTAT ACCOMPLI QUE L'ACTION SERT ENTIÈREMENT.
Si l'action ne sert le RD que partiellement, le RD est trop générique : ne l'associe pas.
Exemples de calibration, à appliquer littéralement :
- Action « Faire une brassée de lavage chaque jour ». RD trop large : « Un domicile principal propre et ordonné en permanence » (servi partiellement). RD juste : « Des vêtements pour la famille propres et bien rangés » (servi entièrement).
- Action « Installer une porte patio au sous-sol arrière du chalet ». RD hors sujet : « Un chalet qui me fait vivre des moments amusants ». RD juste : « Un accès direct du sous-sol vers l'extérieur arrière du chalet ».
Un RD trop large ne génère AUCUNE tension structurelle : l'action rattachée ne tire rien. Rattacher approximativement est plus nuisible que ne pas rattacher.

TROISIÈME MOUVEMENT — PROPOSER UN RD SECONDAIRE :
Tu disposes de trois mouvements, pas deux.
1. Un RD existant satisfait le critère → associe-le (rd_associe_id + rd_associe_titre).
2. Aucun RD existant ne le satisfait, mais tu peux formuler l'état accompli juste → remplis "rd_propose" avec la formulation et le RD parent le plus proche. Tu PROPOSES seulement : ce RD ne sera jamais écrit sans le jugement de Luc (Décision #206 — le triage est un acte humain). Laisse alors rd_associe_id à null.
3. Tu ne peux pas trancher → laisse rd_associe_id et rd_propose à null, et baisse ta confiance.
N'associe JAMAIS un RD trop large en te disant qu'il vaut mieux que rien. Utilise le mouvement 2.

DÉTECTION DE DOUBLON (état courant fourni ci-dessous) :
La liste des Actions ouvertes t'est donnée. Si l'entrée décrit un travail déjà couvert par une Action existante, remplis "doublon_de_action_id" avec son id, et propose la destination "Observation" — la matière est conservée, la tâche n'est pas créée deux fois. Ne signale un doublon que si le TRAVAIL est le même, pas seulement le thème.

BLOC TEMPOREL (noyau minimal #184) :
Si la destination est "Action", propose le bloc temporel où ce geste se pose naturellement, par son id, dans "bloc_suggere_id". Une Action sans prise sur le réel n'est pas exécutable.

RÈGLES GÉNÉRALES :
- Tu PROPOSES, tu ne décides pas (Décision #61, principe 5).
- N'invente jamais un id de RD, d'Action ou de bloc qui n'est pas dans les listes fournies.
- Ton niveau de confiance doit être honnête : une entrée floue = confiance basse + destination "Incertain".
- CAPTURES PAR LIEN OU IMAGE : Luc capture souvent une résonance sous forme de lien seul (article, vidéo YouTube, épingle Pinterest) ou de capture d'écran, sans texte. Ce n'est PAS une entrée vide : le lien EST la capture. Quand le contenu réel de la page t'est fourni, base ton analyse sur ce contenu. Quand des images te sont fournies, regarde-les et analyse ce qu'elles montrent. Une ressource capturée sans texte est presque toujours une "Observation" (matière à conserver), sauf si son contenu révèle clairement un geste à poser.
- Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après, sans balises markdown.

FORMAT DE RÉPONSE (JSON strict) :
{
  "destination": "Action" | "Résultat désiré" | "Réalité actuelle" | "Observation" | "Incertain",
  "confiance": <nombre entier 0-100>,
  "justification": "<1 à 3 phrases expliquant pourquoi>",
  "rd_associe_id": "<recXXX du RD existant qui satisfait le critère de granularité, ou null>",
  "rd_associe_titre": "<titre exact du RD, ou null>",
  "rd_propose": {
    "titre": "<état accompli formulé selon Fritz, sans verbe de processus>",
    "parent_id": "<recXXX du RD parent le plus proche, ou null>",
    "justification": "<pourquoi aucun RD existant ne sert entièrement cette action>"
  },
  "doublon_de_action_id": "<recXXX de l'Action ouverte équivalente, ou null>",
  "bloc_suggere_id": "<recXXX du bloc temporel, ou null>",
  "champs_proposes": {
    "titre": "<formulation proposée de l'Action/RD/RA, en français>",
    "notes": "<précisions utiles, ou null>",
    "echeance_suggeree": "<YYYY-MM-DD si une échéance est évidente dans le texte, sinon null>",
    "recurrente": <true si le geste est explicitement récurrent, sinon false>
  }
}
Mets "rd_propose" à null si tu utilises le mouvement 1 ou 3.`;

    const userPrompt = `ENTRÉE BRUTE À ANALYSER :
"""
${elementCapture.trim() || '(aucun texte — capture par lien ou image)'}
"""
${notes ? `\nNOTES DE CAPTURE :\n"""\n${notes}\n"""\n` : ''}${url ? `\nLIEN CAPTURÉ : ${url}\n` : ''}${contenuLien ? `\nCONTENU RÉEL DE LA PAGE LIÉE :\n"""\n${contenuLien}\n"""\n` : ''}${nomsPieces.length > 0 ? `\nPIÈCES JOINTES : ${nomsPieces.join(', ')}${images.length > 0 ? ' (les images sont fournies ci-jointes — regarde-les)' : ''}\n` : ''}
RÉSULTATS DÉSIRÉS EXISTANTS DANS MVOA :
${rdListText || '(aucun RD listé)'}

ACTIONS OUVERTES EXISTANTES (pour la détection de doublon) :
${actionsListText}

BLOCS TEMPORELS DISPONIBLES :
${blocsListText}`;

    const contenuMessage = images.slice();
    contenuMessage.push({ type: 'text', text: userPrompt });

    const claudeResponse = await fetch(`${ANTHROPIC_API}/v1/messages`, {
      method: 'POST',
      headers: {
        'x-api-key': env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: COMPILE_MODEL,
        max_tokens: 2000,
        system: systemPrompt,
        messages: [{ role: 'user', content: contenuMessage }],
      }),
    });

    if (!claudeResponse.ok) {
      const errText = await claudeResponse.text();
      throw new Error(`Erreur API Claude (${claudeResponse.status}): ${errText.substring(0, 200)}`);
    }

    const claudeData = await claudeResponse.json();
    let rawText = '';
    for (const block of claudeData.content || []) {
      if (block.type === 'text') rawText += block.text;
    }
    rawText = rawText.replace(/```json|```/g, '').trim();

    let proposal;
    try {
      proposal = JSON.parse(rawText);
    } catch (e) {
      throw new Error(`Réponse IA non parsable en JSON : ${rawText.substring(0, 200)}`);
    }

    // Garde-fou : aucun id inventé ne survit.
    const idsRD = new Set(rdList.map(r => r.id));
    const idsActions = new Set(actionsList.map(a => a.id));
    const idsBlocs = new Set(blocsList.map(b => b.id));
    if (proposal.rd_associe_id && !idsRD.has(proposal.rd_associe_id)) {
      proposal.rd_associe_id = null;
      proposal.rd_associe_titre = null;
    }
    if (proposal.doublon_de_action_id && !idsActions.has(proposal.doublon_de_action_id)) {
      proposal.doublon_de_action_id = null;
    }
    if (proposal.bloc_suggere_id && !idsBlocs.has(proposal.bloc_suggere_id)) {
      proposal.bloc_suggere_id = null;
    }
    if (proposal.rd_propose && proposal.rd_propose.parent_id && !idsRD.has(proposal.rd_propose.parent_id)) {
      proposal.rd_propose.parent_id = null;
    }

    let methodeLecture = null;
    if (url.trim()) {
      const m = (contenuLien || '').match(/\(source : ([^)]+)\)\s*$/);
      if (m) methodeLecture = m[1];
      else if ((contenuLien || '').startsWith('(contenu inaccessible')) methodeLecture = 'échec — toutes les méthodes';
      else methodeLecture = 'aucune lecture';
    }

    let titreRempli = null;
    if (!elementCapture.trim()) {
      const matchTitre = (contenuLien || '').match(/TITRE DE LA (?:PAGE|VIDÉO) : ([^\n]+)/);
      const titreRessource = matchTitre ? matchTitre[1].trim().substring(0, 200) : '';
      if (titreRessource) {
        titreRempli = { valeur: titreRessource, provenance: methodeLecture || 'lecture du lien' };
      }
    }

    const compilation = {
      promptVersion: COMPILE_PROMPT_VERSION,
      model: COMPILE_MODEL,
      compiledAt: new Date().toISOString(),
      dependances: {
        rd_consulte_id: proposal.rd_associe_id || null,
        nb_rd_dans_contexte: rdList.length,
        nb_actions_ouvertes_dans_contexte: actionsList.length,
        nb_blocs_dans_contexte: blocsList.length,
        etat_courant_erreur: etatCourantErreur,
        lecture_lien: url.trim() ? {
          methode: methodeLecture,
          caracteres_obtenus: (contenuLien || '').length,
          nature: 'extrait partiel — jamais le contenu intégral garanti',
        } : null,
        images_fournies: images.length,
      },
      titre_rempli: titreRempli,
      proposition: proposal,
      usage: claudeData.usage || null,
    };

    const confiance = Math.max(0, Math.min(100, parseInt(proposal.confiance, 10) || 0));

    // Le doublon et le RD proposé apparaissent dans la note lue par Luc.
    let note = proposal.justification || '';
    if (proposal.doublon_de_action_id) {
      note = `⚠️ DOUBLON PROBABLE d'une Action ouverte existante (${proposal.doublon_de_action_id}). ` + note;
    }
    if (proposal.rd_propose && proposal.rd_propose.titre) {
      note += `\n\n➕ RD SECONDAIRE PROPOSÉ (non écrit) : « ${proposal.rd_propose.titre} »` +
        (proposal.rd_propose.parent_id ? ` — sous le parent ${proposal.rd_propose.parent_id}` : '') +
        (proposal.rd_propose.justification ? `\nRaison : ${proposal.rd_propose.justification}` : '');
    }

    const patchFields = {
      'Type suggéré': proposal.destination || 'Incertain',
      'RD déduit (auto)': proposal.rd_associe_titre || '',
      'Confiance (%)': confiance,
      'Note de traitement': note,
      'Pré-remplissage IA': JSON.stringify(compilation, null, 2),
    };

    if (titreRempli) {
      patchFields['Élément capturé'] = titreRempli.valeur;
    }

    const patchResponse = await fetch(recordUrl, {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${env.AIRTABLE_PAT}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ fields: patchFields, typecast: true }),
    });

    if (!patchResponse.ok) {
      const errText = await patchResponse.text();
      throw new Error(`Écriture Airtable impossible (${patchResponse.status}): ${errText.substring(0, 200)}`);
    }
  } catch (err) {
    await writeCompilationError(recordId, err.message, env);
  }
}

// ============================================================
// /validate — ferme la boucle #248 : Luc décide, le système écrit.
// v23 : accepte et écrit les champs de contexte exigés par #184
//       (bloc temporel, récurrence, échéance, état/énergie) et
//       retourne noyauMinimalComplet pour rendre le manque visible.
// ============================================================
async function handleValidate(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }

  const recordId = body.recordId;
  const decision = body.decision;

  if (!recordId || !/^rec[A-Za-z0-9]{14}$/.test(recordId)) {
    return jsonResponse({ error: 'Paramètre "recordId" requis' }, 400);
  }
  if (!['action', 'observation', 'rejeter'].includes(decision)) {
    return jsonResponse({ error: 'Paramètre "decision" requis : action | observation | rejeter' }, 400);
  }

  const entryUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(ENTREES_BRUTES_TABLE)}/${recordId}`;

  let createdAction = null;
  let noyauMinimalComplet = null;

  if (decision === 'action') {
    const titre = (body.titre || '').trim();
    if (!titre) {
      return jsonResponse({ error: 'Le titre de l\'Action est requis (verrou #184 : pas de prise sur le réel sans action nommée)' }, 400);
    }
    const actionFields = {
      'Action': titre,
      'Entrées brutes': [recordId],
      'Statut': 'À faire',
    };
    if (body.rdId && /^rec[A-Za-z0-9]{14}$/.test(body.rdId)) {
      actionFields['Résultat désiré relié'] = [body.rdId];
    }
    if (body.notes && String(body.notes).trim()) {
      actionFields['Notes'] = String(body.notes).trim();
    }

    // ---- Champs de contexte (#184) ----
    const blocsRecus = []
      .concat(body.blocIds || [])
      .concat(body.blocId ? [body.blocId] : [])
      .filter(id => /^rec[A-Za-z0-9]{14}$/.test(id));
    if (blocsRecus.length > 0) {
      actionFields['Blocs temporels CDU'] = Array.from(new Set(blocsRecus));
    }
    if (body.recurrente === true) {
      actionFields['Récurrente'] = true;
    }
    if (body.echeance && /^\d{4}-\d{2}-\d{2}$/.test(String(body.echeance))) {
      actionFields['Date d\'échéance'] = String(body.echeance);
    }
    if (body.prochaineOccurrence && /^\d{4}-\d{2}-\d{2}$/.test(String(body.prochaineOccurrence))) {
      actionFields['Prochaine occurrence'] = String(body.prochaineOccurrence);
    }
    if (body.etatEnergie && String(body.etatEnergie).trim()) {
      actionFields['État/ Énergie'] = String(body.etatEnergie).trim();
    }

    // #184 : RD lié ET (bloc temporel OU échéance).
    const aRD = Boolean(actionFields['Résultat désiré relié']);
    const aContexteTemps = Boolean(actionFields['Blocs temporels CDU'] || actionFields['Date d\'échéance']);
    noyauMinimalComplet = aRD && aContexteTemps;

    const createUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(ACTIONS_TABLE)}`;
    const createResponse = await fetch(createUrl, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields: actionFields, typecast: true }),
    });
    if (!createResponse.ok) {
      const errText = await createResponse.text();
      return jsonResponse({ error: 'Erreur Airtable à la création de l\'Action', details: errText.substring(0, 300) }, 500);
    }
    createdAction = await createResponse.json();
  }

  const statutMap = {
    'action': 'Traité',
    'observation': 'Observation vivante',
    'rejeter': 'Rejeté',
  };

  const patchResponse = await fetch(entryUrl, {
    method: 'PATCH',
    headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      fields: {
        'Statut traitement': statutMap[decision],
        'Traité?': true,
      },
      typecast: true,
    }),
  });
  if (!patchResponse.ok) {
    const errText = await patchResponse.text();
    return jsonResponse({
      error: 'Action créée mais échec du marquage de l\'entrée',
      actionId: createdAction ? createdAction.id : null,
      details: errText.substring(0, 300),
    }, 500);
  }

  return jsonResponse({
    success: true,
    decision,
    entree: recordId,
    statut: statutMap[decision],
    noyauMinimalComplet,
    actionCreee: createdAction ? { id: createdAction.id, titre: createdAction.fields['Action'] } : null,
  });
}

async function telechargerImageBase64(attachment) {
  const LIMITE_OCTETS = 3500000;
  const candidates = [attachment.url];
  if (attachment.thumbnails && attachment.thumbnails.large && attachment.thumbnails.large.url) {
    candidates.push(attachment.thumbnails.large.url);
  }
  if (attachment.size && attachment.size > LIMITE_OCTETS && candidates.length > 1) {
    candidates.shift();
  }
  for (const urlImage of candidates) {
    try {
      const response = await fetchAvecTimeout(urlImage, 10000, {});
      if (!response.ok) continue;
      const buffer = await response.arrayBuffer();
      if (buffer.byteLength > LIMITE_OCTETS) continue;
      const mediaType = (response.headers.get('content-type') || attachment.type || 'image/jpeg').split(';')[0];
      return {
        type: 'image',
        source: { type: 'base64', media_type: mediaType, data: bufferVersBase64(buffer) },
      };
    } catch (err) {
      continue;
    }
  }
  return null;
}

function bufferVersBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binaire = '';
  const bloc = 0x8000;
  for (let i = 0; i < bytes.length; i += bloc) {
    binaire += String.fromCharCode.apply(null, bytes.subarray(i, i + bloc));
  }
  return btoa(binaire);
}

async function writeCompilationError(recordId, message, env) {
  try {
    const recordUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(ENTREES_BRUTES_TABLE)}/${recordId}`;
    await fetch(recordUrl, {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${env.AIRTABLE_PAT}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        fields: { 'Note de traitement': `⚠️ ERREUR compilation: ${message}` },
      }),
    });
  } catch (e) {
    // Dernier recours : l'erreur sera visible par l'absence de proposition.
  }
}

async function handleDecisionsSearch(url, env) {
  const query = (url.searchParams.get('query') || '').trim();
  const statutParam = url.searchParams.get('statut');
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '8', 10), 20);

  if (!query) {
    return jsonResponse({ error: 'Le paramètre "query" est requis' }, 400);
  }

  const allowedStatuts = statutParam
    ? statutParam.split(',').map(s => s.trim())
    : ['Verrouillé', 'En exploration'];

  const statutFormula = allowedStatuts.length === 1
    ? `{Statut} = '${allowedStatuts[0]}'`
    : `OR(${allowedStatuts.map(s => `{Statut} = '${s}'`).join(', ')})`;

  const records = await fetchAllRecords(MVOA_BASE_ID, DECISIONS_TABLE, statutFormula, env);
  const numeroRefs = [...query.matchAll(/#?\b(\d{1,4})\b/g)].map(m => parseInt(m[1], 10));
  const keywords = query
    .toLowerCase()
    .split(/\s+/)
    .filter(w => w.length >= 2)
    .map(w => w.replace(/[^\wàâäéèêëîïôöùûüç]/g, ''))
    .filter(Boolean);

  const docFreq = {};
  for (const kw of keywords) {
    let count = 0;
    for (const r of records) {
      const blob = ((r.fields['Décision'] || '') + ' ' + (r.fields['Description'] || '') + ' ' + (r.fields['Notes'] || '')).toLowerCase();
      if (blob.includes(kw)) count++;
    }
    docFreq[kw] = count;
  }
  const totalDocs = records.length || 1;

  const scored = records.map(r => {
    const titre = (r.fields['Décision'] || '');
    const titreLow = titre.toLowerCase();
    const description = (r.fields['Description'] || '').toLowerCase();
    const notes = (r.fields['Notes'] || '').toLowerCase();
    const raisons = (r.fields['Raisons'] || '').toLowerCase();
    let score = 0;
    const recordNum = extractDecisionNumber(titre);
    if (recordNum && numeroRefs.includes(recordNum)) score += 10;
    for (const kw of keywords) {
      const df = docFreq[kw] || 1;
      const idf = Math.min(3, Math.log((totalDocs + 1) / (df + 0.5)) + 1);
      if (titreLow.includes(kw)) score += 3 * idf;
      if (description.includes(kw)) score += 1 * idf;
      if (notes.includes(kw)) score += 1 * idf;
      if (raisons.includes(kw)) score += 0.5 * idf;
    }
    const descLen = description.length;
    if (descLen > 1500) {
      const penalty = Math.max(0.85, 1 - (descLen - 1500) / 10000);
      score *= penalty;
    }
    return { record: r, score };
  });

  const matched = scored.filter(s => s.score > 0).sort((a, b) => b.score - a.score);
  const relevant = matched.slice(0, limit);

  const results = relevant.map(({ record, score }) => ({
    id: record.id,
    numero: extractDecisionNumber(record.fields['Décision']),
    titre: record.fields['Décision'] || '',
    description: record.fields['Description'] || '',
    statut: record.fields['Statut'] || '',
    priorite: record.fields['Priorité'] || '',
    date: record.fields['Date'] || '',
    tablesConcernees: record.fields['Tables concernées'] || '',
    score: Math.round(score * 10) / 10,
  }));

  const tronque = matched.length > results.length;

  const disclaimer = results.length === 0
    ? "Aucune décision ne match la query — ne rien inventer."
    : tronque
      ? `Résultats tronqués : ${results.length} retournés sur ${matched.length} correspondances. Aucune pagination disponible — augmenter "limit" (max 20) ou affiner la query.`
      : `${matched.length} correspondance(s) retournée(s) intégralement, sur ${records.length} décisions interrogées. Statuts interrogés : ${allowedStatuts.join(', ')} — les autres statuts sont invisibles pour cette recherche.`;

  return jsonResponse({
    query,
    statuts: allowedStatuts,
    corpusSize: records.length,
    totalMatched: matched.length,
    returned: results.length,
    limit,
    hasMore: tronque,
    pagination: 'absente — plafond dur à 20',
    total: matched.length,
    partial: true,
    disclaimer,
    results,
  });
}

async function handleDecisionsCreate(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }
  const required = ['Décision', 'Description', 'Raisons'];
  const missing = required.filter(f => !body[f] || !String(body[f]).trim());
  if (missing.length > 0) {
    return jsonResponse({ error: 'Champs obligatoires manquants', missing }, 400);
  }
  const statut = body['Statut'] || 'En exploration';
  if (statut === 'Verrouillé') {
    return jsonResponse({ error: 'Statut "Verrouillé" interdit en création directe' }, 403);
  }
  const allRecords = await fetchAllRecords(MVOA_BASE_ID, DECISIONS_TABLE, null, env);
  let maxNum = 0;
  for (const r of allRecords) {
    const n = extractDecisionNumber(r.fields['Décision']);
    if (n && n > maxNum) maxNum = n;
  }
  const nextNum = maxNum + 1;
  let titre = String(body['Décision']).trim();
  if (!titre.match(/^#\d+/)) titre = `#${nextNum} — ${titre}`;
  const fields = {
    'Décision': titre,
    'Description': body['Description'],
    'Raisons': body['Raisons'],
    'Statut': statut,
    'Date': body['Date'] || new Date().toISOString().split('T')[0],
  };
  if (body['Alternatives rejetées']) fields['Alternatives rejetées'] = body['Alternatives rejetées'];
  if (body['Priorité']) fields['Priorité'] = body['Priorité'];
  if (body['Tables concernées']) fields['Tables concernées'] = body['Tables concernées'];
  if (body['Notes']) fields['Notes'] = body['Notes'];
  const createUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(DECISIONS_TABLE)}`;
  const airtableResponse = await fetch(createUrl, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields }),
  });
  if (!airtableResponse.ok) {
    const errText = await airtableResponse.text();
    return jsonResponse({ error: 'Erreur Airtable lors de la création', status: airtableResponse.status, details: errText }, 500);
  }
  const created = await airtableResponse.json();
  return jsonResponse({ success: true, numero: nextNum, titre, statut, id: created.id, fields: created.fields }, 201);
}

async function handleDecisionsLink(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }
  const sourceNum = body['source'] || body['from'];
  const impacts = body['impacts'] || body['to'];
  if (!sourceNum || !Array.isArray(impacts) || impacts.length === 0) {
    return jsonResponse({ error: 'Body invalide' }, 400);
  }
  const allRecords = await fetchAllRecords(MVOA_BASE_ID, DECISIONS_TABLE, null, env);
  const numToId = {};
  for (const r of allRecords) {
    const n = extractDecisionNumber(r.fields['Décision']);
    if (n) numToId[n] = r.id;
  }
  const sourceId = numToId[sourceNum];
  if (!sourceId) return jsonResponse({ error: `Décision source #${sourceNum} introuvable` }, 404);
  const missing = [];
  const impactIds = [];
  for (const num of impacts) {
    const id = numToId[num];
    if (id) impactIds.push(id);
    else missing.push(num);
  }
  if (missing.length > 0) return jsonResponse({ error: "Certaines décisions cibles n'existent pas", missing }, 404);
  const sourceRecord = allRecords.find(r => r.id === sourceId);
  const existingLinks = sourceRecord.fields['Décisions impactées'] || [];
  const existingIds = new Set(existingLinks);
  for (const id of impactIds) existingIds.add(id);
  const finalIds = Array.from(existingIds);
  const patchUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(DECISIONS_TABLE)}/${sourceId}`;
  const airtableResponse = await fetch(patchUrl, {
    method: 'PATCH',
    headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields: { 'Décisions impactées': finalIds } }),
  });
  if (!airtableResponse.ok) {
    const errText = await airtableResponse.text();
    return jsonResponse({ error: 'Erreur Airtable lors du linking', status: airtableResponse.status, details: errText }, 500);
  }
  return jsonResponse({ success: true, source: sourceNum, sourceId, linked: impacts, totalLinks: finalIds.length });
}

async function handleTablesList(env) {
  const metaUrl = `${AIRTABLE_API}/v0/meta/bases/${MVOA_BASE_ID}/tables`;
  const response = await fetch(metaUrl, { headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}` } });
  if (!response.ok) {
    const errText = await response.text();
    return jsonResponse({ error: 'Erreur Airtable Meta API', status: response.status, details: errText }, response.status);
  }
  const data = await response.json();
  const tables = (data.tables || []).map(t => ({
    id: t.id,
    name: t.name,
    description: t.description || '',
    primaryFieldId: t.primaryFieldId,
    fieldsCount: (t.fields || []).length,
    fields: (t.fields || []).map(f => ({ id: f.id, name: f.name, type: f.type })),
    viewsCount: (t.views || []).length,
  }));
  return jsonResponse({ baseId: MVOA_BASE_ID, totalTables: tables.length, tables });
}

async function handleRecordsSearch(url, env) {
  const table = (url.searchParams.get('table') || '').trim();
  const filter = (url.searchParams.get('filter') || '').trim();
  const fieldsParam = (url.searchParams.get('fields') || '').trim();
  const view = (url.searchParams.get('view') || '').trim();
  const maxRecords = Math.min(parseInt(url.searchParams.get('maxRecords') || '50', 10), 200);

  if (!table) {
    return jsonResponse({ error: 'Le paramètre "table" est requis' }, 400);
  }

  const params = new URLSearchParams();
  if (filter) params.set('filterByFormula', filter);
  if (view) params.set('view', view);
  if (fieldsParam) {
    const fieldList = fieldsParam.split(',').map(f => f.trim()).filter(Boolean);
    for (const f of fieldList) params.append('fields[]', f);
  }
  params.set('pageSize', String(Math.min(maxRecords, 100)));

  const allRecords = [];
  let offset = null;
  let pagesFetched = 0;

  do {
    if (offset) params.set('offset', offset);
    const airtableUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(table)}?${params.toString()}`;
    const response = await fetch(airtableUrl, { headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}` } });
    if (!response.ok) {
      const errText = await response.text();
      return jsonResponse({ error: 'Erreur Airtable', status: response.status, details: errText }, response.status);
    }
    const data = await response.json();
    allRecords.push(...(data.records || []));
    offset = data.offset || null;
    pagesFetched++;
    if (allRecords.length >= maxRecords) break;
    if (pagesFetched >= 5) break;
  } while (offset);

  const truncated = allRecords.slice(0, maxRecords);

  return jsonResponse({
    table,
    filter: filter || null,
    totalReturned: truncated.length,
    hasMore: offset !== null && allRecords.length >= maxRecords,
    records: truncated.map(r => ({ id: r.id, createdTime: r.createdTime, fields: r.fields })),
  });
}

async function handleRecordsCreate(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }

  const table = body.table;
  const fields = body.fields;

  if (!table || !fields || typeof fields !== 'object') {
    return jsonResponse({ error: 'Les paramètres "table" et "fields" (objet) sont requis' }, 400);
  }

  if (!ALLOWED_CREATE_TABLES.includes(table)) {
    return jsonResponse({
      error: `Création non autorisée pour la table "${table}"`,
      hint: `MVP limité à : ${ALLOWED_CREATE_TABLES.join(', ')}.`,
    }, 403);
  }

  const createUrl = `${AIRTABLE_API}/v0/${MVOA_BASE_ID}/${encodeURIComponent(table)}`;
  const airtableResponse = await fetch(createUrl, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.AIRTABLE_PAT}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ fields }),
  });

  if (!airtableResponse.ok) {
    const errText = await airtableResponse.text();
    return jsonResponse({
      error: 'Erreur Airtable lors de la création',
      status: airtableResponse.status,
      details: errText,
    }, airtableResponse.status);
  }

  const created = await airtableResponse.json();

  return jsonResponse({
    success: true,
    table,
    id: created.id,
    fields: created.fields,
  }, 201);
}

async function handleClaudeContextualized(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: 'Body JSON invalide' }, 400);
  }

  const userPrompt = body.prompt || body.message;
  if (!userPrompt || !String(userPrompt).trim()) {
    return jsonResponse({
      error: 'Champ "prompt" requis',
    }, 400);
  }

  const systemPromptUser = body.systemPrompt || '';
  const model = body.model || 'claude-sonnet-4-6';
  const maxTokens = body.maxTokens || 4096;
  const maxToolIterations = 6;

  const { dateStr, isoDate } = getCurrentDateContext();

  const governanceBase = `Tu es l'IA contextualisée de MVOA, intégrée via le Worker Cloudflare (Décision #212).

ANCRAGE TEMPOREL — CRITIQUE :
Date actuelle : ${dateStr} (${isoDate})
Fuseau horaire : America/Montreal
Toute date antérieure à ${isoDate} est PASSÉE. Ne jamais qualifier une date passée d'imminente. Si un RD ou une Action a une date dépassée et n'est pas Complété(e), signale-le comme ANOMALIE SYSTÈME.

RÔLE : tu structures, formules, exécutes. Tu ne décides pas. Principe #61 (point 5) : "L'IA propose, les règles contraignent, Luc valide."

GOUVERNANCE #213 : Claude = structuration/exécution. ChatGPT (externe) = critique/contradiction.

RÈGLE STRICTE — INTERDICTION D'INVENTER : si ta réponse dépend de l'état réel de MVOA et que tu n'as pas consulté les outils, dis-le explicitement. Ne jamais halluciner un état non vérifié.

CRITÈRE DE GRANULARITÉ DES RD : le RD juste est le plus petit état accompli que l'action sert entièrement. Si l'action ne sert le RD que partiellement, le RD est trop générique — ne pas l'associer.

RECHERCHE PARTIELLE : search_decisions retourne au maximum 20 résultats, sans pagination, et n'interroge que les statuts Verrouillé et En exploration. Lis les champs returned, totalMatched et hasMore. Si hasMore est vrai, dis-le. Ne jamais conclure qu'une décision n'existe pas à partir d'un résultat vide.

PROPOSITION D'ACTIONS : utilise propose_action pour suggérer une nouvelle Action. Applique le test d'exécutabilité immédiate (#93) avant. Cet outil NE CRÉE RIEN — il stage une proposition éditable que Luc doit confirmer (#221). Seulement des Actions pour l'instant.

OUTILS DISPONIBLES : search_decisions, search_records, list_tables, propose_action.`;

  const finalSystemPrompt = [governanceBase, systemPromptUser].filter(Boolean).join('\n\n---\n\n');

  const messages = [{ role: 'user', content: userPrompt }];
  const toolCallsLog = [];
  const proposals = [];
  let finalAnswer = '';
  let lastUsage = null;
  let iterations = 0;
  let stoppedReason = null;

  while (iterations < maxToolIterations) {
    iterations++;

    const claudePayload = {
      model,
      max_tokens: maxTokens,
      system: finalSystemPrompt,
      messages,
      tools: MVOA_TOOLS,
    };

    const claudeResponse = await fetch(`${ANTHROPIC_API}/v1/messages`, {
      method: 'POST',
      headers: {
        'x-api-key': env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(claudePayload),
    });

    if (!claudeResponse.ok) {
      const errText = await claudeResponse.text();
      return jsonResponse({
        error: 'Erreur Claude API',
        status: claudeResponse.status,
        details: errText,
        toolCalls: toolCallsLog,
      }, 502);
    }

    const claudeData = await claudeResponse.json();
    lastUsage = claudeData.usage || lastUsage;

    let textPart = '';
    const toolUseBlocks = [];
    for (const block of claudeData.content || []) {
      if (block.type === 'text') textPart += block.text;
      if (block.type === 'tool_use') toolUseBlocks.push(block);
    }

    if (claudeData.stop_reason === 'tool_use' && toolUseBlocks.length > 0) {
      messages.push({ role: 'assistant', content: claudeData.content });

      const toolResultsContent = [];
      for (const toolUse of toolUseBlocks) {
        let result;
        if (toolUse.name === 'propose_action') {
          proposals.push({
            fields: toolUse.input.fields || {},
            rationale: toolUse.input.rationale || '',
            executabilityCheck: toolUse.input.executabilityCheck || '',
          });
          result = { status: 'proposal_staged', note: 'Proposition enregistrée pour validation par Luc.' };
        } else {
          result = await executeTool(toolUse.name, toolUse.input, env);
        }
        toolCallsLog.push({ tool: toolUse.name, input: toolUse.input });
        toolResultsContent.push({
          type: 'tool_result',
          tool_use_id: toolUse.id,
          content: JSON.stringify(result),
        });
      }

      messages.push({ role: 'user', content: toolResultsContent });
      continue;
    }

    finalAnswer = textPart;
    stoppedReason = claudeData.stop_reason;
    break;
  }

  if (!finalAnswer && iterations >= maxToolIterations) {
    finalAnswer = '[Plafond atteint sans réponse finale.]';
    stoppedReason = 'max_iterations';
  }

  return jsonResponse({
    success: true,
    answer: finalAnswer,
    model,
    dateContext: isoDate,
    toolCalls: toolCallsLog,
    proposals,
    iterations,
    stopReason: stoppedReason,
    usage: lastUsage,
  });
}

async function searchDecisionsInternal(query, limit, env) {
  const allRecords = await fetchAllRecords(
    MVOA_BASE_ID,
    DECISIONS_TABLE,
    `OR({Statut} = 'Verrouillé', {Statut} = 'En exploration')`,
    env
  );
  const numeroRefs = [...query.matchAll(/#?\b(\d{1,4})\b/g)].map(m => parseInt(m[1], 10));
  const keywords = query.toLowerCase().split(/\s+/).filter(w => w.length >= 2);
  const docFreq = {};
  for (const kw of keywords) {
    let count = 0;
    for (const r of allRecords) {
      const blob = ((r.fields['Décision'] || '') + ' ' + (r.fields['Description'] || '') + ' ' + (r.fields['Notes'] || '')).toLowerCase();
      if (blob.includes(kw)) count++;
    }
    docFreq[kw] = count;
  }
  const totalDocs = allRecords.length || 1;
  const scored = allRecords.map(r => {
    const titre = (r.fields['Décision'] || '');
    const titreLow = titre.toLowerCase();
    const description = (r.fields['Description'] || '').toLowerCase();
    const notes = (r.fields['Notes'] || '').toLowerCase();
    const raisons = (r.fields['Raisons'] || '').toLowerCase();
    let score = 0;
    const recordNum = extractDecisionNumber(titre);
    if (recordNum && numeroRefs.includes(recordNum)) score += 10;
    for (const kw of keywords) {
      const df = docFreq[kw] || 1;
      const idf = Math.min(3, Math.log((totalDocs + 1) / (df + 0.5)) + 1);
      if (titreLow.includes(kw)) score += 3 * idf;
      if (description.includes(kw)) score += 1 * idf;
      if (notes.includes(kw)) score += 1 * idf;
      if (raisons.includes(kw)) score += 0.5 * idf;
    }
    if (description.length > 1500) {
      score *= Math.max(0.85, 1 - (description.length - 1500) / 10000);
    }
    return { record: r, score };
  });

  const matched = scored.filter(s => s.score > 0).sort((a, b) => b.score - a.score);
  const results = matched.slice(0, limit).map(({ record, score }) => ({
    id: record.id,
    numero: extractDecisionNumber(record.fields['Décision']),
    titre: record.fields['Décision'] || '',
    description: record.fields['Description'] || '',
    statut: record.fields['Statut'] || '',
    score: Math.round(score * 10) / 10,
  }));

  return {
    results,
    totalMatched: matched.length,
    corpusSize: allRecords.length,
  };
}

async function fetchAllRecords(baseId, tableName, filterFormula, env, fieldsList) {
  const allRecords = [];
  let offset = null;
  do {
    const params = new URLSearchParams();
    if (filterFormula) params.set('filterByFormula', filterFormula);
    if (fieldsList && fieldsList.length > 0) {
      for (const f of fieldsList) params.append('fields[]', f);
    }
    params.set('pageSize', '100');
    if (offset) params.set('offset', offset);
    const url = `${AIRTABLE_API}/v0/${baseId}/${encodeURIComponent(tableName)}?${params.toString()}`;
    const response = await fetch(url, { headers: { 'Authorization': `Bearer ${env.AIRTABLE_PAT}` } });
    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Airtable error ${response.status}: ${errText}`);
    }
    const data = await response.json();
    allRecords.push(...(data.records || []));
    offset = data.offset || null;
  } while (offset);
  return allRecords;
}

function extractDecisionNumber(titre) {
  if (!titre) return null;
  const match = titre.match(/#(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { ...corsHeaders(), 'Content-Type': 'application/json' },
  });
}

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}
