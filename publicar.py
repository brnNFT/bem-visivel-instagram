#!/usr/bin/env python3
"""Publicador automático do Instagram da Bem Visível.

Como funciona, em uma frase: cada post é uma pasta dentro de `fila/` com o
nome `AAAA-MM-DD_HHMM_nome` (horário de Brasília), as imagens e um
`legenda.txt`. Quando chega a hora, o post é publicado pela API oficial do
Instagram e a pasta vai para `publicados/`.

Comandos:
    python publicar.py verificar          confere a fila sem publicar nada (padrão)
    python publicar.py preparar           converte as imagens para JPEG (exigência da API)
    python publicar.py publicar           publica o que já passou do horário
    python publicar.py publicar --simular finge publicar, sem internet e sem token
    python publicar.py renovar-token      renova o token (vale 60 dias)
    python publicar.py limite             mostra quantos posts a API ainda aceita hoje

Variáveis de ambiente (nunca coloque o token em arquivo do repositório):
    IG_TOKEN        token de acesso do Instagram (obrigatório para publicar)
    IG_USER_ID      id da conta (opcional; descoberto pelo token)
    IG_BASE_URL     endereço público onde as imagens ficam acessíveis,
                    por exemplo https://raw.githubusercontent.com/usuario/repo/main
    IG_API_VERSION  versão da API (padrão v25.0)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
FILA = RAIZ / "fila"
PUBLICADOS = RAIZ / "publicados"

# O Brasil (horário de Brasília) não tem horário de verão desde 2019.
BRASILIA = timezone(timedelta(hours=-3), "BRT")

PASTA_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(?:_(.+))?$")
EXT_IMAGEM = {".jpg", ".jpeg", ".png"}
PASTA_JPG = "_jpg"  # onde ficam as versões JPEG geradas por `preparar`

MAX_IMAGENS = 10  # limite de itens em um carrossel pela API
MAX_LEGENDA = 2200
MAX_HASHTAGS = 30
MAX_MENCOES = 20
MAX_BYTES_JPEG = 8 * 1024 * 1024
PROPORCAO_MIN = 4 / 5  # retrato 4:5
PROPORCAO_MAX = 1.91  # paisagem 1,91:1
MAX_ATRASO = timedelta(hours=24)  # post muito atrasado não sai sozinho


class ErroPublicacao(Exception):
    pass


# ---------------------------------------------------------------- a fila


@dataclass
class Post:
    pasta: Path
    quando: datetime
    imagens: list[Path] = field(default_factory=list)
    legenda: str = ""
    problemas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def nome(self) -> str:
        return self.pasta.name

    @property
    def tem_erro_anterior(self) -> bool:
        return (self.pasta / "erro.txt").exists()

    @property
    def ja_publicado(self) -> bool:
        return (self.pasta / "resultado.json").exists()

    @property
    def tipo(self) -> str:
        return "carrossel" if len(self.imagens) > 1 else "imagem única"

    def jpegs(self) -> list[Path]:
        return [self.pasta / PASTA_JPG / f"{i:02d}.jpg" for i in range(1, len(self.imagens) + 1)]


def ler_post(pasta: Path) -> Post | None:
    m = PASTA_RE.match(pasta.name)
    if not m:
        return None
    ano, mes, dia, hora, minuto, _ = m.groups()
    try:
        quando = datetime(int(ano), int(mes), int(dia), int(hora), int(minuto), tzinfo=BRASILIA)
    except ValueError:
        post = Post(pasta, datetime.max.replace(tzinfo=BRASILIA))
        post.problemas.append("a data ou a hora do nome da pasta não existe")
        return post

    post = Post(pasta, quando)
    post.imagens = sorted(
        (p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXT_IMAGEM),
        key=lambda p: p.name.lower(),
    )
    legenda_arq = pasta / "legenda.txt"
    if legenda_arq.exists():
        post.legenda = legenda_arq.read_text(encoding="utf-8").strip()
    validar(post)
    return post


def validar(post: Post) -> None:
    n = len(post.imagens)
    if n == 0:
        post.problemas.append("a pasta não tem nenhuma imagem (.jpg, .jpeg ou .png)")
    elif n > MAX_IMAGENS:
        post.problemas.append(f"tem {n} imagens; o carrossel aceita no máximo {MAX_IMAGENS}")

    if not (post.pasta / "legenda.txt").exists():
        post.problemas.append("falta o arquivo legenda.txt")
    else:
        if len(post.legenda) > MAX_LEGENDA:
            post.problemas.append(f"a legenda tem {len(post.legenda)} caracteres; o máximo é {MAX_LEGENDA}")
        if len(re.findall(r"#\w+", post.legenda)) > MAX_HASHTAGS:
            post.problemas.append(f"a legenda tem mais de {MAX_HASHTAGS} hashtags")
        if len(re.findall(r"@[\w.]+", post.legenda)) > MAX_MENCOES:
            post.problemas.append(f"a legenda tem mais de {MAX_MENCOES} menções (@)")
        if not post.legenda:
            post.avisos.append("a legenda está vazia")

    proporcoes = []
    for img in post.imagens[:MAX_IMAGENS]:
        tamanho = tamanho_imagem(img)
        if tamanho is None:
            post.avisos.append(f"não consegui ler o tamanho de {img.name}")
            continue
        largura, altura = tamanho
        p = largura / altura
        proporcoes.append(p)
        if not (PROPORCAO_MIN - 0.01 <= p <= PROPORCAO_MAX + 0.01):
            post.problemas.append(
                f"{img.name} tem {largura}x{altura}; o Instagram aceita de 4:5 (retrato) a 1,91:1 (paisagem)"
            )
    if len(set(round(p, 2) for p in proporcoes)) > 1:
        post.avisos.append("as imagens têm proporções diferentes; o Instagram corta todas no formato da primeira")


def tamanho_imagem(caminho: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(caminho) as im:
            return im.size
    except Exception:
        return None


def ler_fila(fila: Path = FILA) -> tuple[list[Post], list[str]]:
    posts, ignoradas = [], []
    if not fila.exists():
        return posts, ignoradas
    for pasta in sorted(p for p in fila.iterdir() if p.is_dir() and not p.name.startswith(".")):
        post = ler_post(pasta)
        if post is None:
            ignoradas.append(pasta.name)
        else:
            posts.append(post)
    posts.sort(key=lambda p: p.quando)
    return posts, ignoradas


def agora() -> datetime:
    return datetime.now(BRASILIA)


# ---------------------------------------------------------------- JPEG


def preparar(post: Post) -> list[Path]:
    """Gera _jpg/01.jpg, 02.jpg... (a API só aceita JPEG). Devolve os arquivos criados."""
    from PIL import Image

    destino = post.pasta / PASTA_JPG
    marca = destino / "origem.txt"  # impressão digital das imagens originais
    digital = "\n".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in post.imagens
    ) + "\n"
    if marca.exists() and marca.read_text() == digital and all(j.exists() for j in post.jpegs()):
        return []
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir()
    criados = []
    for origem, alvo in zip(post.imagens, post.jpegs()):
        with Image.open(origem) as im:
            im = im.convert("RGB")
            qualidade = 95
            while True:
                im.save(alvo, "JPEG", quality=qualidade, optimize=True)
                if alvo.stat().st_size <= MAX_BYTES_JPEG or qualidade <= 60:
                    break
                qualidade -= 10
        criados.append(alvo)
    marca.write_text(digital)
    return criados


# ---------------------------------------------------------------- API


class Instagram:
    """Cliente mínimo da API do Instagram com login do Instagram (graph.instagram.com)."""

    def __init__(self, token: str, versao: str = "v25.0", user_id: str | None = None,
                 espera_s: float = 10, tentativas_status: int = 30):
        self.token = token
        self.base = f"https://graph.instagram.com/{versao}"
        self.espera_s = espera_s
        self.tentativas_status = tentativas_status
        self._user_id = user_id

    # --- HTTP
    def _pedir(self, metodo: str, caminho: str, params: dict | None = None, base: str | None = None) -> dict:
        params = dict(params or {})
        params["access_token"] = self.token
        url = f"{base or self.base}/{caminho.lstrip('/')}"
        dados = urllib.parse.urlencode(params).encode()
        if metodo == "GET":
            req = urllib.request.Request(f"{url}?{dados.decode()}")
        else:
            req = urllib.request.Request(url, data=dados, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            corpo = e.read().decode(errors="replace")
            try:
                erro = json.loads(corpo).get("error", {})
                msg = erro.get("error_user_msg") or erro.get("message") or corpo
            except json.JSONDecodeError:
                msg = corpo
            raise ErroPublicacao(f"a API respondeu {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise ErroPublicacao(f"sem conexão com a API: {e.reason}") from None

    # --- conta
    def user_id(self) -> str:
        if not self._user_id:
            eu = self._pedir("GET", "me", {"fields": "user_id,username"})
            self._user_id = str(eu.get("user_id") or eu["id"])
        return self._user_id

    def conta(self) -> dict:
        return self._pedir("GET", "me", {"fields": "user_id,username"})

    def limite(self) -> dict:
        return self._pedir("GET", f"{self.user_id()}/content_publishing_limit",
                           {"fields": "config,quota_usage"})

    def renovar_token(self) -> dict:
        return self._pedir("GET", "refresh_access_token", {"grant_type": "ig_refresh_token"},
                           base="https://graph.instagram.com")

    # --- publicação
    def _container(self, params: dict) -> str:
        return self._pedir("POST", f"{self.user_id()}/media", params)["id"]

    def _esperar(self, container_id: str) -> None:
        for _ in range(self.tentativas_status):
            st = self._pedir("GET", container_id, {"fields": "status_code,status"})
            codigo = st.get("status_code")
            if codigo == "FINISHED":
                return
            if codigo in ("ERROR", "EXPIRED"):
                raise ErroPublicacao(f"o Instagram recusou a mídia ({codigo}): {st.get('status', '')}")
            time.sleep(self.espera_s)
        raise ErroPublicacao("o Instagram demorou demais para processar a mídia")

    def publicar(self, urls: list[str], legenda: str) -> dict:
        if len(urls) == 1:
            final = self._container({"image_url": urls[0], "caption": legenda})
        else:
            filhos = []
            for url in urls:
                filho = self._container({"image_url": url, "is_carousel_item": "true"})
                self._esperar(filho)
                filhos.append(filho)
            final = self._container({"media_type": "CAROUSEL", "children": ",".join(filhos),
                                     "caption": legenda})
        self._esperar(final)
        media_id = self._pedir("POST", f"{self.user_id()}/media_publish", {"creation_id": final})["id"]
        try:
            link = self._pedir("GET", media_id, {"fields": "permalink"}).get("permalink")
        except ErroPublicacao:
            link = None
        return {"media_id": media_id, "permalink": link}


class InstagramSimulado:
    """Finge ser a API: serve para testar a fila sem token e sem internet."""

    def __init__(self):
        self.chamadas: list[tuple] = []

    def publicar(self, urls: list[str], legenda: str) -> dict:
        self.chamadas.append(("publicar", urls, legenda))
        return {"media_id": "SIMULADO", "permalink": None}


# ---------------------------------------------------------------- comandos


def mostrar(posts: list[Post], ignoradas: list[str], momento: datetime) -> int:
    if not posts and not ignoradas:
        print("A fila está vazia.")
        return 0
    problemas = 0
    for post in posts:
        if post.ja_publicado:
            estado = "JÁ PUBLICADO (falta mover para publicados/)"
        elif post.tem_erro_anterior:
            estado = "PARADO: deu erro antes; leia erro.txt e apague-o para tentar de novo"
        elif post.problemas:
            estado = "COM PROBLEMA"
        elif post.quando <= momento:
            estado = "SAI NA PRÓXIMA RODADA"
        else:
            estado = "agendado"
        print(f"\n{post.quando:%d/%m/%Y %H:%M}  {post.nome}")
        print(f"   {post.tipo}, {len(post.imagens)} imagem(ns), legenda com {len(post.legenda)} caracteres: {estado}")
        for p in post.problemas:
            print(f"   ✗ {p}")
            problemas += 1
        for a in post.avisos:
            print(f"   ! {a}")
    for nome in ignoradas:
        print(f"\n? A pasta '{nome}' foi ignorada: o nome precisa ser AAAA-MM-DD_HHMM_nome, ex.: 2026-11-10_1900_apresentacao")
    print()
    return 1 if problemas else 0


def publicar_pendentes(cliente, base_url: str | None, momento: datetime,
                       fila: Path = FILA, publicados: Path = PUBLICADOS, simular: bool = False) -> int:
    posts, _ = ler_fila(fila)
    pendentes = [p for p in posts if p.quando <= momento and not p.tem_erro_anterior]
    if not pendentes:
        print("Nada para publicar agora.")
        return 0

    falhas = 0
    publicou = False
    for post in pendentes:
        destino = publicados / post.nome
        if post.ja_publicado:  # publicou numa rodada anterior, mas não chegou a mover
            mover(post.pasta, destino, simular)
            continue
        if publicou:  # no máximo um post por rodada, para não sair tudo de uma vez
            print(f"{post.nome} fica para a próxima rodada.")
            continue
        if momento - post.quando > MAX_ATRASO:
            registrar_erro(post, "o horário passou há mais de 24 horas e o post não saiu; "
                           "renomeie a pasta com uma nova data para não publicar fora de hora", simular)
            falhas += 1
            continue
        if post.problemas:
            registrar_erro(post, "; ".join(post.problemas), simular)
            falhas += 1
            continue
        if not simular and not base_url:
            print("Falta IG_BASE_URL (endereço público das imagens).", file=sys.stderr)
            return 2
        if not simular and not all(j.exists() for j in post.jpegs()):
            registrar_erro(post, "as imagens JPEG não foram geradas; rode `python publicar.py preparar` antes", simular)
            falhas += 1
            continue
        base = (base_url or "(simulado)").rstrip("/")
        urls = [f"{base}/{urllib.parse.quote(j.relative_to(fila.parent).as_posix())}" for j in post.jpegs()]

        print(f"Publicando {post.nome} ({post.tipo})...")
        try:
            resultado = cliente.publicar(urls, post.legenda)
        except ErroPublicacao as e:
            registrar_erro(post, str(e), simular)
            falhas += 1
            continue
        publicou = True
        resultado.update({"publicado_em": agora().isoformat(timespec="seconds"), "agendado_para": post.quando.isoformat()})
        if simular:
            print(f"   (simulação) publicaria {len(urls)} imagem(ns): {urls[0]}{' ...' if len(urls) > 1 else ''}")
            continue
        (post.pasta / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   publicado: {resultado.get('permalink') or resultado['media_id']}")
        mover(post.pasta, destino, simular)
    return 1 if falhas else 0


def mover(origem: Path, destino: Path, simular: bool) -> None:
    if simular:
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino = destino.with_name(destino.name + f"_{int(time.time())}")
    shutil.move(str(origem), str(destino))


def registrar_erro(post: Post, mensagem: str, simular: bool) -> None:
    print(f"   ✗ {post.nome}: {mensagem}", file=sys.stderr)
    if not simular:
        (post.pasta / "erro.txt").write_text(
            f"{agora():%d/%m/%Y %H:%M}: {mensagem}\n\n"
            "Corrija o problema e apague este arquivo para o post tentar de novo na próxima rodada.\n",
            encoding="utf-8",
        )


def cliente_real() -> Instagram:
    token = os.environ.get("IG_TOKEN")
    if not token:
        print("Falta a variável IG_TOKEN (o token do Instagram).", file=sys.stderr)
        sys.exit(2)
    return Instagram(token, os.environ.get("IG_API_VERSION", "v25.0"), os.environ.get("IG_USER_ID") or None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publicador automático do Instagram da Bem Visível")
    ap.add_argument("comando", nargs="?", default="verificar",
                    choices=["verificar", "preparar", "publicar", "renovar-token", "limite"])
    ap.add_argument("--simular", action="store_true", help="não chama a API nem mexe nas pastas")
    ap.add_argument("--agora", help="finge que agora é AAAA-MM-DDTHH:MM (horário de Brasília), para testes")
    ap.add_argument("--base-url", default=os.environ.get("IG_BASE_URL"))
    args = ap.parse_args(argv)

    momento = agora()
    if args.agora:
        momento = datetime.fromisoformat(args.agora).replace(tzinfo=BRASILIA)

    if args.comando == "verificar":
        print(f"Agora: {momento:%d/%m/%Y %H:%M} (Brasília)")
        return mostrar(*ler_fila(), momento)

    if args.comando == "preparar":
        posts, _ = ler_fila()
        total = 0
        for post in posts:
            if post.imagens and not post.problemas:
                total += len(preparar(post))
        print(f"{total} imagem(ns) convertida(s) para JPEG.")
        return 0

    if args.comando == "publicar":
        cliente = InstagramSimulado() if args.simular else cliente_real()
        return publicar_pendentes(cliente, args.base_url, momento, simular=args.simular)

    if args.comando == "renovar-token":
        cli = cliente_real()
        r = cli.renovar_token()
        dias = int(r.get("expires_in", 0)) // 86400
        novo = r.get("access_token")
        print(f"Token renovado: vale por mais {dias} dias.")
        if novo and novo != cli.token:
            print("ATENÇÃO: o Instagram devolveu um token NOVO. Atualize o segredo IG_TOKEN com ele.")
            gh_out = os.environ.get("GITHUB_OUTPUT")
            if gh_out:
                with open(gh_out, "a") as f:
                    f.write("token_mudou=true\n")
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::add-mask::{novo}")
            else:
                print(novo)
        return 0

    if args.comando == "limite":
        cli = cliente_real()
        print("Conta:", cli.conta().get("username"))
        print(json.dumps(cli.limite(), indent=2))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
