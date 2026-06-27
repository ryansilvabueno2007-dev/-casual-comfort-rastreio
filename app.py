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

def buscar_pedidos(cpf):
    data_min = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00-03:00")
    pedidos = []
    page = 1
    while page <= 15:
        try:
            r = requests.get(
                f"{BASE_URL}/orders",
                headers=headers(),
                params={"per_page": 200, "page": page, "created_at_min": data_min},
                timeout=20,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        for o in data:
            doc = re.sub(r"\D", "", o.get("contact_document") or "")
            if doc == cpf:
                pedidos.append(o)
        if len(data) < 200:
            break
        page += 1
    return sorted(pedidos, key=lambda x: x.get("created_at", ""), reverse=True)

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
        resultado.append({
            "numero":  p.get("number"),
            "data":    (p.get("created_at") or "")[:10],
            "status":  status_pedido(p),
            "rastreio": rastreio,
            "cidade":  addr.get("city", ""),
            "eventos": buscar_rastreio_jt(rastreio) if rastreio else [],
        })

    return jsonify({"pedidos": resultado})


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
