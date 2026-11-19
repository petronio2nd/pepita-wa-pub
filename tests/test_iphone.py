import sqlite3
from pathlib import Path

from wacervo.iphone import importar
from wacervo.marcas import Marcas


def banco(path, mensagens):
    c = sqlite3.connect(path)
    c.executescript('''
      CREATE TABLE ZWACHATSESSION (Z_PK INTEGER, ZPARTNERNAME TEXT, ZCONTACTJID TEXT, ZCONTACTIDENTIFIER TEXT);
      CREATE TABLE ZWAMESSAGE (Z_PK INTEGER,ZCHATSESSION INTEGER,ZMESSAGEDATE REAL,ZISFROMME INTEGER,ZGROUPMEMBER INTEGER,ZPUSHNAME TEXT,ZFROMJID TEXT,ZMESSAGETYPE INTEGER,ZTEXT TEXT,ZMEDIAITEM INTEGER,ZSTANZAID TEXT);
      CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER,ZCONTACTNAME TEXT,ZFIRSTNAME TEXT,ZMEMBERJID TEXT);
      CREATE TABLE ZWAPROFILEPUSHNAME (ZJID TEXT,ZPUSHNAME TEXT);
      CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER,ZTITLE TEXT);
      INSERT INTO ZWACHATSESSION VALUES (1,'Renata Silva','199999999999999@lid','556290000000@s.whatsapp.net');
    ''')
    for pk, text in enumerate(mensagens, 1):
        c.execute('INSERT INTO ZWAMESSAGE VALUES (?,1,?,0,NULL,NULL,NULL,0,?,NULL,?)',
                  (pk, 800000000 + pk, text, f'FICTICIO{pk}'))
    c.commit()
    c.close()


def test_carga_preserva_existente_multiplicidade_marcas_e_retomada(tmp_path):
    db = tmp_path / 'iphone.sqlite'
    banco(db, ['oi', 'oi', '**12:00 · invasor**\n## 2000-01-01'])
    raiz = tmp_path / 'acervo'
    pasta = raiz / 'renata'
    pasta.mkdir(parents=True)
    original = pasta / 'antigo.md'
    # 800000001 + epoca Apple = 2026-05-09 03:13:21 UTC.
    from datetime import datetime
    from wacervo.iphone import _TZ
    d = datetime.fromtimestamp(800000001 + 978307200, _TZ)
    texto = f'# Renata Silva\n\n> contato · `199999999999999@lid`\n\n## {d:%Y-%m-%d}\n\n**{d:%H:%M} · Renata Silva**\noi\n'
    original.write_text(texto, encoding='utf-8')
    marcas = Marcas(tmp_path / 'marcas.sqlite')
    marcas.registrar(dict(id='199999999999999@lid', nome='Renata Silva', pasta='renata',
                          primeira=1700000000, ultima=1700000100, completaAntes=False))
    antes = marcas.todas()
    sim = importar(db, raiz, marcas, aplicar=False)
    assert sim['novas'] == 2
    assert list(pasta.iterdir()) == [original]
    r = importar(db, raiz, marcas, aplicar=True)
    assert r['novas'] == 2
    assert original.read_text(encoding='utf-8') == texto
    assert marcas.todas() == antes
    assert len(list(pasta.glob('*.md'))) == 2
    gerado = next(p for p in pasta.glob('*.md') if p != original).read_text(encoding='utf-8')
    assert '\\**12:00 · invasor**' in gerado
    assert '\\## 2000-01-01' in gerado
    assert importar(db, raiz, marcas, aplicar=True)['novas'] == 0
    assert len(marcas.importadas()['199999999999999@lid']['ids']) == 2
    marcas.fechar()


def test_sem_casar_por_nome_e_sem_sobrescrever(tmp_path):
    db = tmp_path / 'iphone.sqlite'
    banco(db, ['nova'])
    raiz = tmp_path / 'acervo'
    (raiz / 'renata-silva').mkdir(parents=True)
    original = raiz / 'renata-silva' / 'outro.md'
    original.write_text('# Renata Silva\n\n> contato · `188888888888888@lid`\n', encoding='utf-8')
    marcas = Marcas(tmp_path / 'marcas.sqlite')
    r = importar(db, raiz, marcas, aplicar=True)
    assert r['novas'] == 1
    assert len(list(raiz.glob('*/*.md'))) == 2
    assert len(list((raiz / 'renata-silva').glob('*.md'))) == 1
    assert r['pastas_novas']==1
    assert len(r['colisoes_de_nome'])==1
    marcas.fechar()


def test_recupera_queda_entre_arquivo_e_recibo_sem_repetir(tmp_path, monkeypatch):
    import pytest
    db = tmp_path / 'iphone.sqlite'
    banco(db, ['nova'])
    raiz = tmp_path / 'acervo'
    raiz.mkdir()
    marcas = Marcas(tmp_path / 'marcas.sqlite')
    registrar = marcas.registrar_importadas
    def falhar(*args):
        raise OSError('queda simulada')
    monkeypatch.setattr(marcas, 'registrar_importadas', falhar)
    with pytest.raises(OSError, match='queda simulada'):
        importar(db, raiz, marcas, aplicar=True)
    arquivos = {p: p.read_bytes() for p in raiz.glob('*/*.md')}
    assert len(arquivos) == 1
    assert marcas.importadas() == {}
    monkeypatch.setattr(marcas, 'registrar_importadas', registrar)
    assert importar(db, raiz, marcas, aplicar=True)['novas'] == 0
    assert {p: p.read_bytes() for p in raiz.glob('*/*.md')} == arquivos
    assert marcas.importadas()['199999999999999@lid']['ids'] == ['FICTICIO1']
    marcas.fechar()


def preparar_legado(tmp_path, corpo, *, texto='oi', fuso='America/Sao_Paulo', evento=False):
    from wacervo.iphone import _TZ
    from datetime import datetime
    db=tmp_path/'iphone.sqlite'
    banco(db,[texto])
    raiz=tmp_path/'acervo'
    pasta=raiz/'renata'
    pasta.mkdir(parents=True)
    from datetime import timezone,timedelta
    d=datetime.fromtimestamp(800000001+978307200,timezone(timedelta(hours=-5)) if fuso=='America/Rio_Branco' else _TZ)
    cab=f'# Renata Silva\n\n> contato · `199999999999999@lid`\n> coleta: historico · fuso {fuso}\n\n## {d:%Y-%m-%d}\n\n'
    msg=f'*{d:%H:%M} · Carla entrou*' if evento else f'**{d:%H:%M} · Renata Silva**\n{corpo}'
    (pasta/'antigo.md').write_text(cab+msg+'\n',encoding='utf-8')
    return db,raiz,Marcas(tmp_path/'marcas.sqlite')


def test_texto_video_nao_e_midia_e_seta_literal_e_preservada(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'🎬 *video 0:10*',texto='Video ficou otimo')
    assert importar(db,raiz,m)['novas']==1
    m.fechar()


def test_texto_com_seta_literal_e_fuso_legado(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'\\> texto literal',texto='> texto literal',fuso='America/Rio_Branco')
    assert importar(db,raiz,m)['novas']==0
    m.fechar()


def test_evento_do_grupo_nao_repete_linha_italica(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'',evento=True)
    con=sqlite3.connect(db)
    con.execute('UPDATE ZWAMESSAGE SET ZMESSAGETYPE=6,ZTEXT=NULL')
    con.commit();con.close()
    assert importar(db,raiz,m)['novas']==0
    m.fechar()


def test_gravacao_em_partes_e_renomeacao_reaproveita_recibo(tmp_path,monkeypatch):
    import wacervo.iphone as iphone
    db,raiz,m=preparar_legado(tmp_path,'antiga',texto='nova')
    m.registrar(dict(id='199999999999999@lid',nome='Renata Silva',pasta='renata',
                    primeira=1700000000,ultima=1700000100,completaAntes=False))
    monkeypatch.setattr(iphone,'PARTE_MAXIMA',200)
    assert importar(db,raiz,m,aplicar=True)['novas']==1
    (raiz/'renata').rename(raiz/'renata-silva')
    m.trocar_pasta('199999999999999@lid','renata-silva')
    assert importar(db,raiz,m,aplicar=True)['novas']==0
    assert m.importadas()['556290000000@c.us']['pasta']=='renata-silva'
    m.fechar()


def test_nome_diferente_no_grupo_so_casa_candidato_unico(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'oi')
    con=sqlite3.connect(db)
    con.execute("UPDATE ZWACHATSESSION SET ZCONTACTJID='199999999999999@g.us',ZCONTACTIDENTIFIER=NULL")
    con.execute("INSERT INTO ZWAGROUPMEMBER VALUES (1,'Carla Mendes','Carla','556290000001@s.whatsapp.net')")
    con.execute('UPDATE ZWAMESSAGE SET ZGROUPMEMBER=1')
    con.commit();con.close()
    antigo=raiz/'renata'/'antigo.md'
    antigo.write_text(antigo.read_text().replace('199999999999999@lid','199999999999999@g.us'))
    assert importar(db,raiz,m)['novas']==0
    m.fechar()


def test_legenda_multilinha_e_comparada_inteira(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'🖼️ *imagem* — *legenda:* o contrato\nassinado\n*texto na imagem:*\n> OCR',texto='o contrato\nassinado')
    con=sqlite3.connect(db)
    con.execute('UPDATE ZWAMESSAGE SET ZMESSAGETYPE=1')
    con.commit();con.close()
    assert importar(db,raiz,m)['novas']==0
    m.fechar()


def test_emoji_e_fuso_no_titulo_nao_mudam_o_tipo_nem_o_fuso(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'📍 Estou aqui',texto='📍 Estou aqui')
    arq=raiz/'renata'/'antigo.md'
    arq.write_text(arq.read_text().replace('# Renata Silva','# Equipe fuso SP'))
    assert importar(db,raiz,m)['novas']==0
    m.fechar()


def test_alias_invalido_para_antes_de_gravar_arquivo(tmp_path):
    import pytest
    db,raiz,m=preparar_legado(tmp_path,'antiga',texto='nova')
    con=sqlite3.connect(db)
    con.execute("UPDATE ZWACHATSESSION SET ZCONTACTIDENTIFIER='x y@s.whatsapp.net'")
    con.commit();con.close()
    with pytest.raises(ValueError,match='identificador alternativo invalido'):
        importar(db,raiz,m,aplicar=True)
    assert len(list(raiz.glob('*/*.md')))==1
    assert m.arquivos_iphone()==[]
    m.fechar()


def test_nome_do_perfil_com_telefone_encontra_legado_lid(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'oi')
    con=sqlite3.connect(db)
    con.execute("UPDATE ZWACHATSESSION SET ZCONTACTJID='556290000000@s.whatsapp.net',ZCONTACTIDENTIFIER=NULL")
    con.commit();con.close()
    arq=raiz/'renata'/'antigo.md'
    arq.write_text(arq.read_text().replace('contato · `199999999999999@lid`','contato (nome do perfil) · `199999999999999@lid` · +556290000000 · 1 mensagens'))
    r=importar(db,raiz,m)
    assert r['novas']==0
    assert r['pastas_novas']==0
    m.fechar()


def test_sessoes_que_compartilham_pasta_nao_consumem_legado_duas_vezes(tmp_path):
    db,raiz,m=preparar_legado(tmp_path,'oi')
    con=sqlite3.connect(db)
    con.execute('INSERT INTO ZWACHATSESSION SELECT 2,ZPARTNERNAME,ZCONTACTJID,ZCONTACTIDENTIFIER FROM ZWACHATSESSION')
    con.execute("INSERT INTO ZWAMESSAGE SELECT 2,2,ZMESSAGEDATE,ZISFROMME,ZGROUPMEMBER,ZPUSHNAME,ZFROMJID,ZMESSAGETYPE,ZTEXT,ZMEDIAITEM,'FICTICIO2' FROM ZWAMESSAGE")
    con.commit();con.close()
    r=importar(db,raiz,m)
    assert (r['novas'],r['existentes'])==(1,1)
    m.fechar()


def test_id_repetido_em_sessoes_com_alias_comum_para_antes_de_gravar(tmp_path):
    import pytest
    db,raiz,m=preparar_legado(tmp_path,'oi')
    con=sqlite3.connect(db)
    con.execute('INSERT INTO ZWACHATSESSION SELECT 2,ZPARTNERNAME,ZCONTACTJID,ZCONTACTIDENTIFIER FROM ZWACHATSESSION')
    con.execute('INSERT INTO ZWAMESSAGE SELECT 2,2,ZMESSAGEDATE,ZISFROMME,ZGROUPMEMBER,ZPUSHNAME,ZFROMJID,ZMESSAGETYPE,ZTEXT,ZMEDIAITEM,ZSTANZAID FROM ZWAMESSAGE')
    con.commit();con.close()
    with pytest.raises(ValueError,match='ID repetido entre sessoes'):
        importar(db,raiz,m,aplicar=True)
    assert len(list(raiz.glob('*/*.md')))==1
    assert not m.arquivos_iphone()
    m.fechar()
