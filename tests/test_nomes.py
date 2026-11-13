from wacervo.nomes import propor, slug


def _m(nome, pasta):
    return {"nome": nome, "pasta": pasta}


def test_slug_como_o_js():
    assert slug("Conceição & Cia. 2") == "conceicao-cia-2"
    assert slug("🙂") == "conversa"


def test_propoe_so_pasta_de_numero_com_nome():
    marcas = {
        "556290000000@c.us": _m("Dra Renata Silva", "55-62-9000-0000"),
        "556290000001@c.us": _m("+55 62 9000-0001", "55-62-9000-0001"),
        "556290000002@c.us": _m("Carla Mendes", "carla-mendes"),
    }
    linhas = propor(marcas)
    assert [(x["pasta"], x["nova"]) for x in linhas] == [("55-62-9000-0000", "dra-renata-silva")]


def test_homonimos_levam_o_numero():
    marcas = {
        "556290000000@c.us": _m("Renata", "55-62-9000-0000"),
        "556290000001@c.us": _m("Renata", "55-62-9000-0001"),
        "556290000002@c.us": _m("Carla", "carla"),
    }
    novas = {x["pasta"]: x["nova"] for x in propor(marcas)}
    assert novas == {"55-62-9000-0000": "renata-556290000000", "55-62-9000-0001": "renata-556290000001"}


def test_destino_ocupado_apos_o_numero_ganha_sufixo():
    marcas = {
        "5511@c.us": _m("Ana", "5511"),
        "5522@c.us": _m("Ana", "55-22"),
        "5533@c.us": _m("Outro", "ana-5511"),
    }
    novas = {x["pasta"]: x["nova"] for x in propor(marcas)}
    assert novas["5511"] == "ana-5511-2"
    assert novas["55-22"] == "ana-5522"


def test_aplicar_renomeia_pasta_md_e_marca(tmp_path):
    from wacervo.marcas import Marcas
    from wacervo.nomes import aplicar, propor

    marcas = Marcas(tmp_path / "m.sqlite3")
    marcas.registrar({"id": "556290000000@c.us", "nome": "Renata Silva", "pasta": "55-62-9000-0000",
                      "primeira": 1, "ultima": 2, "completaAntes": True})
    pasta = tmp_path / "dest" / "55-62-9000-0000"
    (pasta / "anexos").mkdir(parents=True)
    (pasta / "55-62-9000-0000_2026-09-17.md").write_text("x", encoding="utf-8")
    r = aplicar(propor(marcas.todas()), tmp_path / "dest", marcas)
    assert r["renomeadas"] == 1 and r["arquivos"] == 1
    assert (tmp_path / "dest" / "renata-silva" / "renata-silva_2026-09-17.md").exists()
    assert (tmp_path / "dest" / "renata-silva" / "anexos").is_dir()
    assert marcas.todas()["556290000000@c.us"]["pasta"] == "renata-silva"
    assert aplicar(propor(marcas.todas()), tmp_path / "dest", marcas)["renomeadas"] == 0
    marcas.fechar()
