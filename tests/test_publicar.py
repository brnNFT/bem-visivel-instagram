"""Testes do publicador. Rodar com: python -m unittest -v"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publicar as pub  # noqa: E402
from PIL import Image  # noqa: E402


def criar_post(fila: Path, nome: str, n_imagens=1, legenda="Olá #BemVisivel", tamanho=(1080, 1350)):
    pasta = fila / nome
    pasta.mkdir(parents=True)
    for i in range(1, n_imagens + 1):
        Image.new("RGB", tamanho, (30, 59, 52)).save(pasta / f"tela{i}.png")
    if legenda is not None:
        (pasta / "legenda.txt").write_text(legenda, encoding="utf-8")
    return pasta


class APIFalsa(pub.Instagram):
    """Responde como a API do Instagram, sem internet, e anota cada chamada."""

    def __init__(self, falhar_em=None):
        super().__init__("TOKEN", user_id="123", espera_s=0)
        self.chamadas = []
        self.falhar_em = falhar_em
        self._n = 0

    def _pedir(self, metodo, caminho, params=None, base=None):
        self.chamadas.append((metodo, caminho, dict(params or {})))
        if self.falhar_em and self.falhar_em in caminho:
            raise pub.ErroPublicacao("a API respondeu 400: imagem inválida")
        if caminho.endswith("/media"):
            self._n += 1
            return {"id": f"C{self._n}"}
        if caminho.endswith("/media_publish"):
            return {"id": "M1"}
        if params and params.get("fields") == "permalink":
            return {"permalink": "https://www.instagram.com/p/abc/"}
        return {"status_code": "FINISHED"}


class TestFila(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.fila = self.raiz / "fila"
        self.publicados = self.raiz / "publicados"
        self.fila.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def momento(self, s):
        return datetime.fromisoformat(s).replace(tzinfo=pub.BRASILIA)

    def test_le_data_do_nome_da_pasta(self):
        criar_post(self.fila, "2026-11-12_1900_apresentacao", 4)
        (self.fila / "rascunho").mkdir()
        posts, ignoradas = pub.ler_fila(self.fila)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].quando, self.momento("2026-11-12T19:00"))
        self.assertEqual(posts[0].tipo, "carrossel")
        self.assertEqual(posts[0].problemas, [])
        self.assertEqual(ignoradas, ["rascunho"])

    def test_aponta_problemas(self):
        criar_post(self.fila, "2026-11-12_1900_sem-legenda", 1, legenda=None)
        criar_post(self.fila, "2026-11-13_1900_muitas", 11)
        criar_post(self.fila, "2026-11-14_1900_alta", 1, tamanho=(1080, 1920))
        criar_post(self.fila, "2026-11-15_1900_hashtags", 1, legenda=" ".join(f"#t{i}" for i in range(31)))
        posts, _ = pub.ler_fila(self.fila)
        self.assertTrue(all(p.problemas for p in posts), [p.problemas for p in posts])

    def test_preparar_gera_jpeg_uma_vez(self):
        criar_post(self.fila, "2026-11-12_1900_a", 2)
        post = pub.ler_fila(self.fila)[0][0]
        self.assertEqual(len(pub.preparar(post)), 2)
        self.assertEqual(pub.preparar(post), [])  # nada mudou: não refaz
        with Image.open(post.jpegs()[0]) as im:
            self.assertEqual(im.format, "JPEG")

    def publicar(self, cliente, quando, simular=False):
        return pub.publicar_pendentes(cliente, "https://exemplo/repo/abc", self.momento(quando),
                                      fila=self.fila, publicados=self.publicados, simular=simular)

    def test_carrossel_segue_os_passos_da_api(self):
        criar_post(self.fila, "2026-11-12_1900_apresentacao", 3)
        pub.preparar(pub.ler_fila(self.fila)[0][0])
        api = APIFalsa()
        self.assertEqual(self.publicar(api, "2026-11-12T19:05"), 0)

        criados = [c for c in api.chamadas if c[1].endswith("/media")]
        self.assertEqual(len(criados), 4)  # 3 filhos + 1 carrossel
        self.assertTrue(all(c[2].get("is_carousel_item") == "true" for c in criados[:3]))
        self.assertEqual(criados[0][2]["image_url"],
                         "https://exemplo/repo/abc/fila/2026-11-12_1900_apresentacao/_jpg/01.jpg")
        self.assertEqual(criados[3][2]["media_type"], "CAROUSEL")
        self.assertEqual(criados[3][2]["children"], "C1,C2,C3")
        self.assertEqual(criados[3][2]["caption"], "Olá #BemVisivel")
        self.assertIn(("POST", "123/media_publish", {"creation_id": "C4"}), api.chamadas)

        feito = self.publicados / "2026-11-12_1900_apresentacao"
        self.assertTrue(feito.exists())
        self.assertFalse((self.fila / "2026-11-12_1900_apresentacao").exists())
        self.assertEqual(json.loads((feito / "resultado.json").read_text())["media_id"], "M1")

    def test_nao_publica_antes_da_hora_e_um_por_rodada(self):
        for nome in ["2026-11-12_1900_a", "2026-11-12_1930_b", "2026-11-20_1900_c"]:
            pub.preparar(pub.ler_post(criar_post(self.fila, nome)))
        api = APIFalsa()
        self.publicar(api, "2026-11-12T20:00")
        self.assertEqual(sorted(p.name for p in self.publicados.iterdir()), ["2026-11-12_1900_a"])
        self.publicar(api, "2026-11-12T20:15")
        self.assertEqual(len(list(self.publicados.iterdir())), 2)
        self.assertTrue((self.fila / "2026-11-20_1900_c").exists())

    def test_post_muito_atrasado_nao_sai(self):
        pasta = criar_post(self.fila, "2026-11-12_1900_a")
        pub.preparar(pub.ler_post(pasta))
        api = APIFalsa()
        self.assertEqual(self.publicar(api, "2026-11-14T19:00"), 1)
        self.assertEqual(api.chamadas, [])
        self.assertTrue((pasta / "erro.txt").exists())

    def test_erro_da_api_para_o_post_ate_corrigir(self):
        pasta = criar_post(self.fila, "2026-11-12_1900_a")
        pub.preparar(pub.ler_post(pasta))
        self.assertEqual(self.publicar(APIFalsa(falhar_em="/media"), "2026-11-12T19:05"), 1)
        self.assertIn("imagem inválida", (pasta / "erro.txt").read_text())
        api = APIFalsa()
        self.publicar(api, "2026-11-12T19:20")
        self.assertEqual(api.chamadas, [])  # não tenta de novo sozinho
        (pasta / "erro.txt").unlink()
        self.publicar(api, "2026-11-12T19:35")
        self.assertTrue((self.publicados / "2026-11-12_1900_a").exists())

    def test_simulacao_nao_mexe_em_nada(self):
        pasta = criar_post(self.fila, "2026-11-12_1900_a")
        sim = pub.InstagramSimulado()
        self.assertEqual(self.publicar(sim, "2026-11-12T19:05", simular=True), 0)
        self.assertEqual(len(sim.chamadas), 1)
        self.assertTrue(pasta.exists())
        self.assertFalse(self.publicados.exists())


if __name__ == "__main__":
    unittest.main()
