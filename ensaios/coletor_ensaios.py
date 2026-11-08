"""Coletor dos ensaios finais do TCC.

Fica aberto durante todos os ensaios e grava, nesta pasta:
  registros/registro_<inicio>_<parte>.jsonl  todo resultado da placa (5 por segundo pela USB),
                                              mudancas de estado/modo/conexao e o painel a cada 5 s;
  prints/<nome>.png                            capturas de tela pedidas por arquivo;
  coletor_status.json                          resumo atualizado a cada 2 s.

Pedidos (arquivos criados em pedidos/):
  <nome>.print  -> tira um print da tela inteira e salva em prints/<nome>.png
  <nome>.marca  -> registra uma marca com o texto do arquivo (inicio de bloco, observacao)
  auto_captura.on / auto_estado.on -> print automatico 0,7 s apos cada mudanca da fase da
                   captura / dos estados; o texto do arquivo e quantos prints tirar (padrao 1)

Nao altera nada na placa nem no servidor: so le http://127.0.0.1:8765/api/estado.
Feche a janela (ou Ctrl+C) para encerrar.
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765") + "/api/estado"
REGISTROS, PRINTS, PEDIDOS = AQUI / "registros", AQUI / "prints", AQUI / "pedidos"
for p in (REGISTROS, PRINTS, PEDIDOS):
    p.mkdir(exist_ok=True)
INTERVALO = 0.08          # ~12 consultas por segundo (a placa envia 5 resultados por segundo)
ROTACAO_S = 300           # um arquivo novo a cada 5 minutos
inicio = datetime.now().strftime("%Y%m%d_%H%M%S")

PS_PRINT = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class D { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[D]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save('{destino}', [System.Drawing.Imaging.ImageFormat]::Png)
"""


class Registro:
    def __init__(self):
        self.parte, self.aberto_em, self.f = 0, 0.0, None
        self.linhas = 0
        self.trava = threading.Lock()

    def escrever(self, obj):
        with self.trava:
            self._escrever(obj)

    def _escrever(self, obj):
        agora = time.time()
        if self.f is None or agora - self.aberto_em > ROTACAO_S:
            if self.f:
                self.f.close()
            self.parte += 1
            self.aberto_em = agora
            self.f = open(REGISTROS / f"registro_{inicio}_{self.parte:03d}.jsonl", "a", encoding="utf-8")
        obj = dict(t_pc=round(agora, 3), **obj)
        self.f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.f.flush()
        self.linhas += 1

    def fechar(self):
        with self.trava:
            self._fechar()

    def _fechar(self):
        if self.f:
            self.f.close()


def ler_estado():
    with urllib.request.urlopen(URL, timeout=2) as r:
        return json.loads(r.read().decode("utf-8"))


def tirar_print(nome):
    destino = PRINTS / (nome + ".png")
    n = 2
    while destino.exists():
        destino = PRINTS / f"{nome}_{n}.png"
        n += 1
    script = PS_PRINT.replace("{destino}", str(destino).replace("'", "''"))
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, timeout=30)
    return destino if destino.exists() else None, (r.stderr or "").strip()[:300]


def print_em_thread(reg, nome, atraso=0.0):
    # Em outra thread, para nao abrir lacunas no registro enquanto o print e salvo.
    def fazer():
        if atraso:
            time.sleep(atraso)
        try:
            destino, erro = tirar_print(nome)
        except Exception as exc:  # PowerShell ausente ou tela bloqueada
            destino, erro = None, str(exc)[:300]
        reg.escrever(dict(tipo="print", nome=nome, arquivo=destino.name if destino else None, erro=erro or None))
        print("\n" + time.strftime("%H:%M:%S"), "print:", destino.name if destino else "FALHOU " + erro, flush=True)
    reg.escrever(dict(tipo="print_pedido", nome=nome))
    threading.Thread(target=fazer, daemon=True).start()


def auto_print(reg, flag, nome):
    # pedidos/<flag>.on liga o print automatico; o texto do arquivo e quantos prints ainda tirar.
    f = PEDIDOS / (flag + ".on")
    if not f.exists():
        return
    try:
        restantes = int((f.read_text(encoding="utf-8").strip() or "1"))
    except (OSError, ValueError):
        restantes = 1
    print_em_thread(reg, nome, atraso=0.7)
    try:
        if restantes <= 1:
            f.unlink()
        else:
            f.write_text(str(restantes - 1), encoding="utf-8")
    except OSError:
        pass


def tratar_pedidos(reg):
    for p in sorted(PEDIDOS.glob("*")):
        if p.suffix not in (".print", ".marca"):
            continue
        try:
            texto = p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if p.suffix == ".marca":
            reg.escrever(dict(tipo="marca", nome=p.stem, texto=texto))
            print(time.strftime("%H:%M:%S"), "marca:", p.stem, texto[:80], flush=True)
        else:
            print_em_thread(reg, p.stem)
        try:
            p.unlink()
        except OSError:
            pass


def resumo_painel(s):
    cap = s.get("capture") or {}
    return dict(estados=s.get("states"), modo=s.get("mode_confirmed"), modo_pendente=s.get("mode_pending"),
                cenario=s.get("scenario"), captura=cap.get("phase"), captura_id=cap.get("id"),
                usb=(s.get("serial") or {}).get("connected"), wifi=(s.get("wifi") or {}).get("online"),
                mqtt=(s.get("mqtt") or {}).get("connected"), erro=s.get("last_error"))


def desligar_quickedit():
    # No Windows, um clique no console entra em modo "Selecionar" e congela o programa ao
    # escrever na tela. Desliga esse modo so neste console.
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        modo = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(modo)):
            k.SetConsoleMode(h, (modo.value & ~0x0040) | 0x0080)
    except Exception:
        pass


def main():
    desligar_quickedit()
    reg = Registro()
    ultimo_res, ultimo_resumo = None, None
    ultimo_painel = ultimo_status = 0.0
    n_res = falhas = lacunas = 0
    seq_ant = {}
    print("Coletor dos ensaios gravando em", REGISTROS, flush=True)
    print("Deixe esta janela aberta durante os ensaios.", flush=True)
    try:
        while True:
            t = time.time()
            try:
                s = ler_estado()
                falhas = 0
            except Exception as exc:  # servidor fechado ou ocupado: tenta de novo
                falhas += 1
                if falhas in (1, 100):
                    reg.escrever(dict(tipo="falha_consulta", erro=str(exc)[:200]))
                time.sleep(0.5)
                continue
            r = s.get("result") or {}
            chave = (r.get("boot"), r.get("seq"), r.get("tipo"), r.get("captura_id"), r.get("entrada_a_seguir"))
            if r and chave != ultimo_res:
                ultimo_res = chave
                boot, seq = r.get("boot"), r.get("seq")
                if isinstance(seq, int) and boot in seq_ant and seq > seq_ant[boot] + 1 and not r.get("entrada_a_seguir"):
                    lacunas += seq - seq_ant[boot] - 1
                if isinstance(seq, int):
                    seq_ant[boot] = seq
                reg.escrever(dict(tipo="resultado", r=r))
                n_res += 1
            resumo = resumo_painel(s)
            if resumo != ultimo_resumo:
                reg.escrever(dict(tipo="painel_mudou", **resumo))
                hora = time.strftime("%H%M%S")
                if ultimo_resumo and resumo["captura"] != ultimo_resumo["captura"]:
                    auto_print(reg, "auto_captura", f"auto_captura_{resumo['captura']}_{hora}")
                if ultimo_resumo and resumo["estados"] != ultimo_resumo["estados"]:
                    e = resumo["estados"] or []
                    auto_print(reg, "auto_estado", f"auto_estado_{''.join(str(x) for x in e)}_{hora}")
                ultimo_resumo = resumo
            if t - ultimo_painel > 5:
                reg.escrever(dict(tipo="painel", s={k: v for k, v in s.items() if k != "result"}))
                ultimo_painel = t
            if (PEDIDOS / "parar.on").exists():
                (PEDIDOS / "parar.on").unlink()
                reg.escrever(dict(tipo="marca", nome="parar", texto="coletor encerrado por pedido"))
                break
            if t - ultimo_status > 2:
                try:
                    tratar_pedidos(reg)
                except Exception as exc:
                    reg.escrever(dict(tipo="falha_pedidos", erro=str(exc)[:200]))
                status = dict(atualizado=datetime.now().isoformat(timespec="seconds"), resultados=n_res,
                              lacunas_seq=lacunas, linhas=reg.linhas, parte=reg.parte, **resumo,
                              ultimo_seq=r.get("seq"), irms=r.get("irms_adc"), decisao=r.get("decisao"),
                              transporte=r.get("transporte", "usb"))
                try:  # o Google Drive pode prender o arquivo durante a sincronizacao
                    (AQUI / "coletor_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1),
                                                              encoding="utf-8")
                except OSError:
                    pass
                print(f"\r{time.strftime('%H:%M:%S')}  resultados {n_res}  lacunas {lacunas}  estados {resumo['estados']}  "
                      f"IRMS {r.get('irms_adc', 0) or 0:7.1f}  {str(r.get('decisao'))[:20]:20s}", end="", flush=True)
                ultimo_status = t
            time.sleep(max(0.0, INTERVALO - (time.time() - t)))
    except KeyboardInterrupt:
        pass
    finally:
        reg.fechar()


if __name__ == "__main__":
    main()
