# Gera os icones da extensao. Rode da raiz do repositorio:
#   uv run --no-project --with pillow python extensao/icones/gerar.py extensao/icones
# (tambem grava previa-icone.png na mesma pasta, para conferir; nao versione).
import sys
from pathlib import Path
from PIL import Image, ImageDraw

destino = Path(sys.argv[1])
destino.mkdir(parents=True, exist_ok=True)
AMP = 8  # supersampling


def desenhar(tam):
    S = tam * AMP
    p = lambda x: round(x * S)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # fundo: quadrado arredondado com degrade verde
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    topo, base = (18, 170, 130), (6, 94, 84)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(topo, base)) + (255,))
    mascara = Image.new("L", (S, S), 0)
    margem = 0 if tam <= 16 else p(0.03)
    ImageDraw.Draw(mascara).rounded_rectangle([margem, margem, S - 1 - margem, S - 1 - margem], radius=p(0.22), fill=255)
    img.paste(grad, (0, 0), mascara)

    d = ImageDraw.Draw(img)
    branco = (255, 255, 255, 255)
    verde = (7, 110, 94, 255)

    # balao de conversa com rabinho
    x0, y0, x1, y1 = 0.17, 0.18, 0.83, 0.66
    d.rounded_rectangle([p(x0), p(y0), p(x1), p(y1)], radius=p(0.15), fill=branco)
    d.polygon([(p(0.25), p(0.60)), (p(0.19), p(0.83)), (p(0.44), p(0.64))], fill=branco)

    # seta para baixo (exportar)
    cx = 0.5
    d.rectangle([p(cx - 0.045), p(0.27), p(cx + 0.045), p(0.46)], fill=verde)
    d.polygon([(p(cx - 0.15), p(0.42)), (p(cx + 0.15), p(0.42)), (p(cx), p(0.575))], fill=verde)

    # pepita de ouro no canto (some no 16 px, onde viraria borrao)
    if tam > 16:
        ouro_escuro, ouro, brilho = (176, 120, 20, 255), (242, 184, 44, 255), (255, 236, 150, 255)
        pepita = [(0.62, 0.74), (0.68, 0.64), (0.78, 0.61), (0.88, 0.66), (0.91, 0.76), (0.85, 0.87), (0.72, 0.89), (0.63, 0.83)]
        d.polygon([(p(x), p(y)) for x, y in pepita], fill=ouro_escuro)
        cxp, cyp = 0.765, 0.755
        interno = [(cxp + (x - cxp) * 0.82, cyp + (y - cyp) * 0.82) for x, y in pepita]
        d.polygon([(p(x), p(y)) for x, y in interno], fill=ouro)
        d.polygon([(p(0.70), p(0.69)), (p(0.77), p(0.66)), (p(0.80), p(0.70)), (p(0.73), p(0.73))], fill=brilho)

    return img.resize((tam, tam), Image.LANCZOS)


for tam in (16, 32, 48, 128):
    desenhar(tam).save(destino / f"icone-{tam}.png")
# previa ampliada para conferir
prev = Image.new("RGBA", (16 + 32 + 48 + 128 + 50, 140), (40, 40, 40, 255))
x = 10
for tam in (16, 32, 48, 128):
    prev.paste(desenhar(tam), (x, 6), desenhar(tam))
    x += tam + 10
prev.save(destino / "previa-icone.png")
