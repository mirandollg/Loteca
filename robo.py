# -*- coding: utf-8 -*-
"""
LOTECA - robô do GitHub Actions.

Roda sozinho de tempos em tempos e grava no repositório:
  dados/concursos.json      grade dos últimos concursos + resultados oficiais (quando a Caixa publica)
  dados/indice_escudos.json índice nome -> imagem montado a partir do escudosfc.com.br
  dados/escudos.json        escudo de cada clube que já apareceu na grade (arquivo em escudos/)
  escudos/*.png|gif|jpg     as imagens dos escudos

Correções manuais de escudo: edite dados/escudos_manual.json no GitHub, no formato
  {"botafogo-sp": "https://escudosfc.com.br/.../imagem.gif"}
(a chave aparece no app quando você toca no escudo).

Teste local:  python robo.py
"""
import os, sys, json, re, ssl, time, unicodedata, mimetypes
import urllib.request, urllib.parse
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(BASE, "dados")
ESC_DIR = os.path.join(BASE, "escudos")
os.makedirs(DADOS, exist_ok=True); os.makedirs(ESC_DIR, exist_ok=True)
CONC_FILE = os.path.join(DADOS, "concursos.json")
INDEX_FILE = os.path.join(DADOS, "indice_escudos.json")
ESC_FILE = os.path.join(DADOS, "escudos.json")
MANUAL_FILE = os.path.join(DADOS, "escudos_manual.json")

CAIXA = os.environ.get("CAIXA_API", "https://servicebus2.caixa.gov.br/portaldeloterias/api/loteca")
ESC_SITE = os.environ.get("ESC_SITE", "https://escudosfc.com.br/")
ESC_START = ["bras.htm", "bra.htm", "index.html"]
ESC_MAX_PAGES = int(os.environ.get("PAGINAS", "400"))
INDICE_DIAS = 7          # remonta o índice de escudos uma vez por semana
GUARDAR = 12             # quantos concursos manter no arquivo
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 LotecaPessoal/1.0",
      "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9"}
# ------------------------------------------------------------------ rede
def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
SSL = _ssl_ctx()

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL) as r:
        return r.read(), r.headers.get("Content-Type", "")

def http_json(url):
    raw, _ = http_get(url)
    return json.loads(raw.decode("utf-8", "replace"))

def decode_html(raw, ctype=""):
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I) or re.search(rb'charset=["\']?([\w-]+)', raw[:3000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else None
    for e in [enc, "utf-8", "latin-1"]:
        if not e:
            continue
        try:
            return raw.decode(e)
        except Exception:
            pass
    return raw.decode("latin-1", "replace")

# ------------------------------------------------------------------ texto
def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

PEQ = {"de", "da", "do", "das", "dos", "e"}
ACENTOS = {"atletico": "atlético", "america": "américa", "sao": "são", "gremio": "grêmio", "cuiaba": "cuiabá", "maringa": "maringá",
    "ferroviaria": "ferroviária", "vitoria": "vitória", "ceara": "ceará", "goias": "goiás", "avai": "avaí", "criciuma": "criciúma",
    "parana": "paraná", "nautico": "náutico", "operario": "operário", "goiania": "goiânia", "amazonas": "amazonas", "tombense": "tombense",
    "suica": "suíça", "grecia": "grécia", "servia": "sérvia", "eslovenia": "eslovênia", "eslovaquia": "eslováquia", "republica": "república",
    "pais": "país", "italia": "itália", "belgica": "bélgica", "croacia": "croácia", "japao": "japão", "colombia": "colômbia",
    "mexico": "méxico", "austria": "áustria", "polonia": "polônia", "romenia": "romênia", "ucrania": "ucrânia", "suecia": "suécia",
    "finlandia": "finlândia", "islandia": "islândia", "franca": "frança", "escocia": "escócia", "irlanda": "irlanda", "bulgaria": "bulgária",
    "albania": "albânia", "macedonia": "macedônia", "bosnia": "bósnia", "georgia": "geórgia", "armenia": "armênia", "estonia": "estônia",
    "letonia": "letônia", "lituania": "lituânia", "marrocos": "marrocos", "argelia": "argélia", "tunisia": "tunísia", "nigeria": "nigéria",
    "camaroes": "camarões", "africa": "áfrica", "coreia": "coreia", "arabia": "arábia", "ira": "irã", "jordania": "jordânia",
    "uzbequistao": "uzbequistão", "canada": "canadá", "panama": "panamá", "peru": "peru", "bolivia": "bolívia", "chile": "chile",
    "atletico-go": "atlético-go", "sevilla": "sevilla", "alaves": "alavés", "leganes": "leganés", "monchengladbach": "mönchengladbach",
    "penarol": "peñarol", "inter": "inter", "botafogo": "botafogo"}
def bonito(nome):
    nome = (nome or "").strip()
    nome = re.sub(r"/[A-Z]{2,3}$", "", nome)          # "SERVIA/SER" -> "SERVIA"
    ws = [ACENTOS.get(w, w) for w in nome.lower().split()]
    return " ".join(w if (w in PEQ and i) else w[:1].upper() + w[1:] for i, w in enumerate(ws))

def slug(s):
    return norm(s).replace(" ", "-") or "time"

def ler(fp, padrao):
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao

def gravar(fp, obj, indent=None):
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, fp)

# ------------------------------------------------------------------ Caixa
def jogo_da_caixa(j):
    uf1, uf2 = (j.get("siglaUFUm") or "").strip(), (j.get("siglaUFDois") or "").strip()
    casa, fora = bonito(j.get("nomeEquipeUm")), bonito(j.get("nomeEquipeDois"))
    return {
        "seq": j.get("nuSequencial"),
        "casa": casa + ("/" + uf1 if uf1 else ""),
        "fora": fora + ("/" + uf2 if uf2 else ""),
        "paisCasa": (j.get("siglaPaisUm") or "").strip(),
        "paisFora": (j.get("siglaPaisDois") or "").strip(),
        "info": ((j.get("diaSemana") or "")[:3] + " " + (j.get("dtJogo") or "")[:5]).strip(),
    }

def resultado_de(g1, g2):
    if g1 is None or g2 is None:
        return None
    return "1" if g1 > g2 else ("2" if g2 > g1 else "X")


def programacao():
    d = http_json(CAIXA + "/programacao")
    jogos = sorted(d.get("listaJogos") or [], key=lambda j: j.get("nuSequencial") or 0)
    if len(jogos) != 14:
        raise ValueError("a Caixa devolveu %d jogos" % len(jogos))
    return {"numero": int(d.get("nuConcurso")), "fimApostas": "%s %sh" % (d.get("dataFimApostas") or "", d.get("horarioFimApostas") or ""),
            "jogos": [jogo_da_caixa(j) for j in jogos]}

def resultado(num):
    """None se o concurso ainda não foi apurado."""
    try:
        d = http_json("%s/%d" % (CAIXA, int(num)))
    except Exception:
        return None
    lst = sorted(d.get("listaResultadoEquipeEsportiva") or [], key=lambda j: j.get("nuSequencial") or 0)
    if len(lst) != 14 or not d.get("listaRateioPremio"):
        return None
    return {"numero": int(num), "jogos": [jogo_da_caixa(j) for j in lst], "apuracao": d.get("dataApuracao") or "",
            "resultados": [resultado_de(j.get("nuGolEquipeUm"), j.get("nuGolEquipeDois")) for j in lst],
            "placares": ["%s x %s" % (j.get("nuGolEquipeUm"), j.get("nuGolEquipeDois")) for j in lst],
            "rateio": [{"faixa": r.get("descricaoFaixa"), "ganhadores": r.get("numeroDeGanhadores"), "premio": r.get("valorPremio")}
                       for r in (d.get("listaRateioPremio") or [])]}

def atualizar_concursos():
    conc = ler(CONC_FILE, {})
    try:
        p = programacao()
        k = str(p["numero"])
        conc[k] = dict(conc.get(k, {}), **p)
        print("Grade do concurso", k, "ok")
    except Exception as e:
        print("Grade: não consegui buscar (%s)" % e)
    # resultados: concursos guardados que ainda não têm resultado
    for k in sorted(conc, key=int, reverse=True)[:4]:
        if not conc[k].get("resultados"):
            r = resultado(int(k))
            if r:
                jogos_antigos = conc[k].get("jogos")
                conc[k].update(r)
                if jogos_antigos: conc[k]["jogos"] = jogos_antigos
                print("Resultado do concurso", k, "ok")
    for k in sorted(conc, key=int)[:-GUARDAR]:
        conc.pop(k)
    gravar(CONC_FILE, conc)
    return conc

# ------------------------------------------------------------------ escudos: leitura do site
# ------------------------------------------------------------------ escudos: leitura do site
class EscudosParser(HTMLParser):
    """Lê páginas em tabela: cada imagem é ligada ao nome do clube (alt/title, texto da mesma
    célula ou da célula vizinha). Guarda também o texto da linha inteira (cidade, estado)."""
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.page = page_url
        self.links, self.found = set(), []
        self.row, self.cell, self.in_row = [], None, False
        self.loose_img = None

    def _new_cell(self):
        self.cell = {"imgs": [], "texts": []}
        self.row.append(self.cell)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.add(urllib.parse.urljoin(self.page, a["href"].strip()))
        if tag == "tr":
            self._flush()
            self.in_row = True
        elif tag in ("td", "th"):
            if not self.in_row:
                self.in_row = True
            self._new_cell()
        elif tag == "img" and a.get("src"):
            src = urllib.parse.urljoin(self.page, a["src"].strip())
            if not re.search(r"\.(gif|png|jpe?g|webp|svg)(\?|$)", src, re.I):
                return
            if re.search(r"(tit_|banner|bandeira|flag|seta|botao|button|logo_?site|fundo|spacer)", src, re.I):
                return
            img = {"src": src, "alt": (a.get("alt") or a.get("title") or "").strip()}
            if self.cell is not None:
                self.cell["imgs"].append(img)
            else:
                self.loose_img = img

    def handle_data(self, data):
        t = re.sub(r"\s+", " ", data or "").strip()
        if not t:
            return
        if self.cell is not None:
            self.cell["texts"].append(t)
        elif self.loose_img is not None:
            self._add(self.loose_img["alt"] or t, self.loose_img["src"], t)
            self.loose_img = None

    def handle_endtag(self, tag):
        if tag in ("tr", "table"):
            self._flush()

    def _add(self, label, src, ctx):
        label = re.sub(r"\s+", " ", label or "").strip(" -:|")
        n = norm(label)
        if not n or len(n) > 60 or not re.search(r"[a-z]", n):
            return
        self.found.append({"nome": label, "n": n, "url": src, "ctx": norm(ctx)[:160], "pag": self.page.rsplit("/", 1)[-1]})

    def _flush(self):
        if self.row:
            row_txt = " ".join(" ".join(c["texts"]) for c in self.row)
            for i, c in enumerate(self.row):
                for img in c["imgs"]:
                    label = img["alt"]
                    if not label and c["texts"]:
                        label = c["texts"][0]
                    if not label and i + 1 < len(self.row) and self.row[i + 1]["texts"] and not self.row[i + 1]["imgs"]:
                        label = self.row[i + 1]["texts"][0]
                    if not label and i > 0 and self.row[i - 1]["texts"] and not self.row[i - 1]["imgs"]:
                        label = self.row[i - 1]["texts"][-1]
                    if label:
                        self._add(label, img["src"], row_txt)
        self.row, self.cell, self.in_row = [], None, False


STATUS = {"paginas": 0, "escudos": 0}
def crawl(max_pages=ESC_MAX_PAGES, verbose=False):
    """Percorre o escudosfc (só páginas .htm/.html do próprio site) e monta o índice nome -> imagem."""
    STATUS.update(paginas=0, escudos=0)
    host = urllib.parse.urlparse(ESC_SITE).netloc
    fila = [urllib.parse.urljoin(ESC_SITE, p) for p in ESC_START]
    vistos, achados = set(), {}
    try:
        while fila and len(vistos) < max_pages:
            url = fila.pop(0).split("#")[0]
            if url in vistos:
                continue
            vistos.add(url)
            try:
                raw, ct = http_get(url, timeout=20)
            except Exception as e:
                if verbose:
                    print("  falhou:", url, e)
                continue
            p = EscudosParser(url)
            try:
                p.feed(decode_html(raw, ct)); p.close()
            except Exception:
                pass
            for e in p.found:
                achados[(e["n"], e["url"])] = e
            for l in sorted(p.links):
                u = urllib.parse.urlparse(l)
                if u.netloc == host and re.search(r"\.(htm|html)$", u.path, re.I) and l not in vistos:
                    # prioridade para páginas do Brasil
                    (fila.insert(0, l) if re.search(r"/bra", u.path, re.I) else fila.append(l))
            STATUS.update(paginas=len(vistos), escudos=len(achados))
            if verbose and len(vistos) % 20 == 0:
                print("  %d páginas, %d escudos" % (len(vistos), len(achados)))
            time.sleep(0.15)
        idx = list(achados.values())
        if len(idx) >= 20 or not os.path.exists(INDEX_FILE):
            gravar(INDEX_FILE, {"gerado": time.strftime("%Y-%m-%d"), "itens": idx})
        print("Índice de escudos: %d páginas, %d escudos" % (len(vistos), len(idx)))
    except Exception as e:
        print("Índice de escudos: erro", e)


def indice():
    d = ler(INDEX_FILE, None)
    return d.get("itens", []) if isinstance(d, dict) else (d or [])

def indice_velho():
    d = ler(INDEX_FILE, None)
    if not isinstance(d, dict) or not d.get("itens"):
        return True
    try:
        return (time.time() - time.mktime(time.strptime(d["gerado"], "%Y-%m-%d"))) > INDICE_DIAS * 86400
    except Exception:
        return True

# ------------------------------------------------------------------ escudos: casar nome da Caixa com o site
# ------------------------------------------------------------------ escudos: casar nome da Caixa com o site
UF_PALAVRAS = {
    "AC": "acre rio branco", "AL": "alagoas maceio alagoano", "AM": "amazonas manaus", "AP": "amapa macapa",
    "BA": "bahia salvador baiano feira de santana", "CE": "ceara fortaleza cearense juazeiro", "DF": "brasilia distrito federal gama",
    "ES": "espirito santo vitoria capixaba cariacica", "GO": "goias goiania goiano goianiense anapolis", "MA": "maranhao sao luis",
    "MG": "minas mineiro belo horizonte uberlandia juiz de fora ipatinga", "MS": "mato grosso do sul campo grande",
    "MT": "mato grosso cuiaba", "PA": "para belem paraense", "PB": "paraiba joao pessoa campina grande paraibano",
    "PE": "pernambuco recife pernambucano caruaru", "PI": "piaui teresina", "PR": "parana curitiba paranaense londrina maringa cascavel ponta grossa",
    "RJ": "rio de janeiro carioca volta redonda", "RN": "rio grande do norte natal potiguar", "RO": "rondonia porto velho",
    "RR": "roraima boa vista", "RS": "rio grande do sul porto alegre gaucho caxias pelotas", "SC": "santa catarina florianopolis catarinense joinville chapeco itajai brusque criciuma",
    "SE": "sergipe aracaju sergipano", "SP": "sao paulo paulista campinas ribeirao preto santos limeira araraquara bragança braganca mirassol novo horizonte sorocaba",
    "TO": "tocantins palmas",
}
APELIDOS = {  # nome curto da Caixa (+UF) -> nomes completos mais comuns
    ("atletico", "GO"): ["atletico goianiense", "atletico go", "atletico clube goianiense"],
    ("atletico", "MG"): ["atletico mineiro", "atletico mg", "clube atletico mineiro"],
    ("athletico", "PR"): ["athletico paranaense", "athletico pr", "atletico paranaense"],
    ("america", "MG"): ["america mineiro", "america mg", "america futebol clube"],
    ("america", "RN"): ["america rn", "america de natal"],
    ("botafogo", "RJ"): ["botafogo", "botafogo de futebol e regatas"],
    ("botafogo", "SP"): ["botafogo sp", "botafogo ribeirao preto", "botafogo futebol clube"],
    ("botafogo", "PB"): ["botafogo pb", "botafogo paraiba", "botafogo futebol clube"],
    ("inter limeira", "SP"): ["inter de limeira", "internacional de limeira", "inter limeira"],
    ("internacional", "RS"): ["internacional", "sport club internacional"],
    ("bragantino", "SP"): ["red bull bragantino", "bragantino", "rb bragantino"],
    ("sport", "PE"): ["sport", "sport recife", "sport club do recife"],
    ("vitoria", "BA"): ["vitoria", "esporte clube vitoria"],
    ("vasco da gama", "RJ"): ["vasco da gama", "vasco"],
    ("operario", "PR"): ["operario ferroviario", "operario pr"],
    ("ferroviaria", "SP"): ["ferroviaria", "associacao ferroviaria"],
    ("ponte preta", "SP"): ["ponte preta", "associacao atletica ponte preta"],
}


def chave(nome, uf):
    return slug(nome) + ("-" + uf.lower() if uf else "")

def achar_escudo(nome, uf="", pais=""):
    """Devolve (entrada_do_indice, pontuação) ou (None, 0)."""
    idx = indice()
    if not idx:
        return None, 0
    key = norm(nome)
    uf = (uf or "").upper()
    apel = [norm(x) for x in APELIDOS.get((key, uf), [])]
    uf_words = set(norm(UF_PALAVRAS.get(uf, "")).split())
    best, best_s = None, 0
    for e in idx:
        n, ctx, pag = e["n"], e.get("ctx", ""), e.get("pag", "").lower()
        if n in apel:
            s = 110 - apel.index(n)
        elif n == key:
            s = 100
        elif n.startswith(key + " "):
            s = 62
        elif (" " + key + " ") in (" " + n + " "):
            s = 45
        else:
            continue
        if uf:
            toks = set((n + " " + ctx).split())
            if uf.lower() in n.split():
                s += 30
            elif uf_words & toks:
                s += 18
            if pag.startswith("bra"):
                s += 8
        if "fem" in pag:
            s -= 60
        if s > best_s:
            best, best_s = e, s
    return (best, best_s) if best_s >= 60 else (None, best_s)


IMG_EXT = {"image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}

def baixar_imagem(url, k):
    raw, ct = http_get(url, timeout=20)
    ct = (ct or "").split(";")[0].strip().lower()
    if not ct.startswith("image/"):
        ct = mimetypes.guess_type(url)[0] or ""
    if not ct.startswith("image/") or len(raw) < 60:
        raise ValueError("o link não é uma imagem")
    for f in os.listdir(ESC_DIR):
        if f.rsplit(".", 1)[0] == k:
            os.remove(os.path.join(ESC_DIR, f))
    nome = k + IMG_EXT.get(ct, ".png")
    with open(os.path.join(ESC_DIR, nome), "wb") as f:
        f.write(raw)
    return "escudos/" + nome

def eh_selecao(nome):
    return norm(nome) in _SEL
_SEL = {norm(x) for x in ["brasil","argentina","uruguai","paraguai","chile","bolivia","peru","equador","colombia","venezuela","mexico","estados unidos","eua","canada","costa rica","panama","honduras","jamaica","haiti","curacao","portugal","espanha","franca","alemanha","italia","inglaterra","escocia","pais de gales","gales","irlanda do norte","irlanda","holanda","paises baixos","belgica","suica","austria","dinamarca","suecia","noruega","finlandia","islandia","polonia","republica tcheca","tchequia","eslovaquia","hungria","romenia","bulgaria","servia","croacia","eslovenia","bosnia","bosnia e herzegovina","montenegro","macedonia do norte","albania","kosovo","grecia","turquia","ucrania","russia","georgia","israel","chipre","luxemburgo","estonia","letonia","lituania","marrocos","argelia","tunisia","egito","senegal","nigeria","gana","camaroes","costa do marfim","cabo verde","africa do sul","japao","coreia do sul","china","australia","nova zelandia","arabia saudita","ira","iraque","catar","jordania","uzbequistao"]}

def atualizar_escudos(conc):
    esc = ler(ESC_FILE, {})
    manual = ler(MANUAL_FILE, {})
    times = {}
    for c in conc.values():
        for j in c.get("jogos", []):
            for lado in ("casa", "fora"):
                nome = j.get(lado, "")
                base, uf = (nome.split("/")[0], nome.split("/")[1]) if "/" in nome else (nome, "")
                if not uf and eh_selecao(base):
                    continue
                times[chave(base, uf)] = (base, uf, j.get("pais" + lado.capitalize(), ""))
    for k, (base, uf, pais) in sorted(times.items()):
        url_manual = manual.get(k)
        atual = esc.get(k) or {}
        if url_manual and atual.get("origem") != url_manual:
            try:
                esc[k] = {"nome": base + ("/" + uf if uf else ""), "arquivo": baixar_imagem(url_manual, k), "origem": url_manual, "manual": True}
                print("  %-26s escudo manual" % k)
            except Exception as e:
                print("  %-26s link manual falhou (%s)" % (k, e))
            continue
        if atual.get("arquivo") and os.path.exists(os.path.join(BASE, atual["arquivo"])):
            continue
        e, s = achar_escudo(base, uf, pais)
        if not e:
            print("  %-26s NÃO ACHEI (pont. %d)" % (k, s))
            continue
        try:
            esc[k] = {"nome": base + ("/" + uf if uf else ""), "arquivo": baixar_imagem(e["url"], k), "origem": e["url"], "achado": e["nome"]}
            print("  %-26s %s" % (k, e["nome"]))
        except Exception as ex:
            print("  %-26s falhou ao baixar (%s)" % (k, ex))
    gravar(ESC_FILE, esc, indent=1)

if __name__ == "__main__":
    conc = atualizar_concursos()
    if indice_velho() or "--indice" in sys.argv:
        crawl(max_pages=ESC_MAX_PAGES, verbose=True)
    atualizar_escudos(conc)
    if not os.path.exists(MANUAL_FILE):
        gravar(MANUAL_FILE, {}, indent=1)
    d = ler(INDEX_FILE, {})
    gravar(os.path.join(DADOS, "status.json"), {
        "atualizado": time.strftime("%d/%m/%Y %H:%M", time.gmtime(time.time() - 3 * 3600)),   # horário de Brasília
        "concursos": sorted(conc, key=int), "escudos": len(ler(ESC_FILE, {})),
        "indice": len(d.get("itens", [])) if isinstance(d, dict) else 0,
        "indiceGerado": d.get("gerado", "") if isinstance(d, dict) else ""})
