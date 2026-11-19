"""Fixtures comuns.

SEG-10: o `wa servir` recusa quem vem sem `Origin` e sem o cabecalho `X-Pepita-WA`. Os testes
falam com ele como programa local (sem `Origin`), entao o `http.client` ganha o cabecalho
sozinho. O teste que prova a recusa usa `@pytest.mark.sem_cabecalho_proprio` (ou socket cru).

M2 (auditoria MCP, 14/11/2026): sem `Origin`, POST /transcrever, /ocr, /gravar, /rotina, /marca,
/rodada, /lote, /rotina/vez e /rotina/fim exigem o token de dados/agente.token. O servidor dos testes usa `TOKEN_TESTE`, e o `http.client` o manda
sozinho (fora das rotas /agente/, que testam o token por conta propria). O teste que prova a recusa
usa `@pytest.mark.sem_token`.
"""

import http.client

import pytest

import wacervo.servidor as modulo_servidor

TOKEN_TESTE = "t" * 43


def pytest_configure(config):
    config.addinivalue_line("markers", "sem_cabecalho_proprio: nao poe o X-Pepita-WA nos pedidos do http.client")
    config.addinivalue_line("markers", "sem_token: nao poe o X-Wacervo-Token nos pedidos do http.client")


@pytest.fixture(autouse=True)
def _cabecalho_proprio(request, monkeypatch):
    monkeypatch.setattr(modulo_servidor, "criar_token", lambda caminho: TOKEN_TESTE)
    com_cabecalho = not request.node.get_closest_marker("sem_cabecalho_proprio")
    com_token = not request.node.get_closest_marker("sem_token")
    original = http.client.HTTPConnection.putrequest

    def putrequest(self, method, url, *args, **kwargs):
        original(self, method, url, *args, **kwargs)
        if com_cabecalho:
            self.putheader("X-Pepita-WA", "1")
        if com_token and not url.startswith("/agente/"):
            self.putheader("X-Wacervo-Token", TOKEN_TESTE)

    monkeypatch.setattr(http.client.HTTPConnection, "putrequest", putrequest)
