import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

STORE_ID = os.environ.get("NUVEMSHOP_STORE_ID", "")
TOKEN    = os.environ.get("NUVEMSHOP_ACCESS_TOKEN", "")
BASE_URL = f"https://api.nuvemshop.com.br/v1/{STORE_ID}"

def headers():
    return {
        "Authentication": f"bearer {TOKEN}",
        "User-Agent": "CasualComfort/1.0",
        "Content-Type": "application/json",
    }

STATUS_ENVIO = {
    "unpacked":    "Preparando pedido",
    "shipped":     "Enviado",
    "delivered":   "Entregue",
    "undelivered": "Não entregue",
    "returned":    "Devolvido",
}
STATUS_PAGAMENTO = {
    "pending":    "Aguardando pagamento",
    "authorized": "Pagamento autorizado",
    "paid":       "Pago",
    "voided":     "Cancelado",
    "refunded":   "Reembolsado",
    "abandoned":  "Abandonado",
}
STATUS_PEDIDO = {
    "open":      "Em aberto",
    "closed":    "Finalizado",
    "cancelled": "Cancelado",
}

def status_pedido(order):
    s = STATUS_ENVIO.get(order.get("shipping_status") or "")
    if s: return s
    s = STATUS_PAGAMENTO.get(order.get("payment_status") or "")
    if s: return s
    return STATUS_PEDIDO.get(order.get("status") or "", "Em processamento")

def extrair_cpf(order):
    for campo in [
        order.get("contact_document"),
        order.get("contact_identification"),
        (order.get("customer") or {}).get("identification"),
        (order.get("billing_address") or {}).get("document"),
    ]:
        if campo:
            return re.sub(r"\D", "", campo)
    return ""

def buscar_pedidos(cpf):
    data_min = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00-03:00")
    pedidos = []
    page = 1
    while page <= 5:
        try:
            r = requests.get(
                f"{BASE_URL}/orders",
                headers=headers(),
                params={"per_page": 50, "page": page, "created_at_min": data_min},
                timeout=25,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        for o in data:
            if extrair_cpf(o) == cpf:
                pedidos.append(o)
        if len(data) < 50:
            break
        page += 1
    return sorted(pedidos, key=lambda x: x.get("created_at", ""), reverse=True)

def detectar_transportadora(codigo):
    if not codigo:
        return "desconhecida"
    c = codigo.strip()
    if c.startswith("888"):
        return "jt"
    if re.match(r"^[A-Z]{2}\d{9}BR$", c.upper()):
        return "correios"
    return "desconhecida"

def buscar_rastreio_correios(codigo):
    try:
        r = requests.get(
            f"https://api.linketrack.com/track/json?user=teste&token=1abcd&codigo={codigo}",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            eventos = []
            for ev in (data.get("eventos") or []):
                data_str = ev.get("data", "") + " " + ev.get("hora", "")
                local = ev.get("origem", {})
                local_str = f"{local.get('cidade','')}/{local.get('uf','')}" if isinstance(local, dict) else str(local)
                eventos.append({
                    "data": data_str.strip(),
                    "status": ev.get("descricao", ""),
                    "local": local_str,
                })
            return eventos
    except Exception:
        pass
    return []

def buscar_rastreio_jt(codigo):
    try:
        r = requests.post(
            "https://www.jtexpress.com.br/ordertrack/trajectory/list",
            json={"waybillNos": [codigo]},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                eventos = [
                    {"data": t.get("scanDate", ""), "status": t.get("scanDesc", ""), "local": t.get("scanAddr", "")}
                    for t in (item.get("traceList") or [])
                ]
                if eventos:
                    return eventos
    except Exception:
        pass
    return []

def buscar_rastreio(codigo):
    if not codigo:
        return [], "desconhecida"
    transp = detectar_transportadora(codigo)
    if transp == "correios":
        return buscar_rastreio_correios(codigo), "correios"
    if transp == "jt":
        return buscar_rastreio_jt(codigo), "jt"
    eventos = buscar_rastreio_jt(codigo) or buscar_rastreio_correios(codigo)
    return eventos, "desconhecida"


@app.route("/rastrear")
def rastrear():
    cpf = re.sub(r"\D", "", request.args.get("cpf", ""))
    if len(cpf) != 11:
        return jsonify({"erro": "CPF inválido."}), 400

    pedidos = buscar_pedidos(cpf)
    if not pedidos:
        return jsonify({"erro": "Nenhum pedido encontrado nos últimos 90 dias."}), 404

    resultado = []
    for p in pedidos[:5]:
        rastreio = (p.get("shipping_tracking_number") or "").strip()
        addr = p.get("shipping_address") or {}
        eventos, transp = buscar_rastreio(rastreio)
        resultado.append({
            "numero":       p.get("number"),
            "data":         (p.get("created_at") or "")[:10],
            "status":       status_pedido(p),
            "rastreio":     rastreio,
            "transportadora": transp,
            "cidade":       addr.get("city", ""),
            "eventos":      eventos,
        })

    return jsonify({"pedidos": resultado})


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/debug")
def debug():
    resultado = {
        "python_version": "",
        "store_id": STORE_ID[:4] + "***" if STORE_ID else "NAO_DEFINIDO",
        "token_ok": bool(TOKEN),
        "nuvemshop": {"status": None, "erro": None},
        "jt": {"status": None, "erro": None},
    }
    import sys
    resultado["python_version"] = sys.version

    try:
        r = requests.get(
            f"{BASE_URL}/orders",
            headers=headers(),
            params={"per_page": 1, "page": 1},
            timeout=15,
        )
        resultado["nuvemshop"]["status"] = r.status_code
        if r.status_code == 200:
            pedidos = r.json()
            if pedidos:
                p = pedidos[0]
                resultado["nuvemshop"]["amostra_pedido"] = {
                    "numero": p.get("number"),
                    "contact_document": p.get("contact_document"),
                    "contact_identification": p.get("contact_identification"),
                    "contact_email": (p.get("contact_email") or "")[:6] + "***",
                    "customer_cpf": (p.get("customer") or {}).get("identification"),
                    "billing_document": (p.get("billing_address") or {}).get("document"),
                    "todos_campos": [k for k in p.keys()],
                }
        else:
            resultado["nuvemshop"]["erro"] = r.text[:300]
    except Exception as e:
        resultado["nuvemshop"]["erro"] = str(e)

    try:
        r2 = requests.post(
            "https://www.jtexpress.com.br/ordertrack/trajectory/list",
            json={"waybillNos": ["TEST"]},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resultado["jt"]["status"] = r2.status_code
    except Exception as e:
        resultado["jt"]["erro"] = str(e)

    return jsonify(resultado)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
