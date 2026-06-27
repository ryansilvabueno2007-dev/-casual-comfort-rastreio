import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

NS_STORE_ID = os.environ.get("NUVEMSHOP_STORE_ID", "")
NS_TOKEN    = os.environ.get("NUVEMSHOP_ACCESS_TOKEN", "")
NS_BASE     = f"https://api.nuvemshop.com.br/v1/{NS_STORE_ID}"
NS_HEADERS  = lambda: {
    "Authentication": f"bearer {NS_TOKEN}",
    "User-Agent": "CasualComfort-Rastreio/1.0",
    "Content-Type": "application/json",
}

STATUS_MAP = {
    "open":      "Aberto",
    "closed":    "Finalizado",
    "cancelled": "Cancelado",
}
PAGAMENTO_MAP = {
    "pending":    "Aguardando pagamento",
    "authorized": "Pagamento autorizado",
    "paid":       "Pago",
    "voided":     "Cancelado",
    "refunded":   "Reembolsado",
    "abandoned":  "Abandonado",
}
ENVIO_MAP = {
    "unpacked":   "Preparando pedido",
    "shipped":    "Enviado",
    "delivered":  "Entregue",
    "undelivered":"Nao entregue",
    "returned":   "Devolvido",
}


def buscar_pedidos_cpf(cpf):
    data_min = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00-03:00")
    pedidos  = []
    page     = 1
    while page <= 15:
        resp = requests.get(
            f"{NS_BASE}/orders",
            headers=NS_HEADERS(),
            params={"per_page": 200, "page": page, "created_at_min": data_min},
            timeout=20,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        if not data:
            break
        for order in data:
            doc = re.sub(r"\D", "", order.get("contact_document") or "")
            if doc == cpf:
                pedidos.append(order)
        if len(data) < 200:
            break
        page += 1
    return sorted(pedidos, key=lambda x: x.get("created_at", ""), reverse=True)


def buscar_eventos_jt(codigo):
    try:
        resp = requests.post(
            "https://www.jtexpress.com.br/ordertrack/trajectory/list",
            json={"waybillNos": [codigo]},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data") or []
            for item in items:
                traces = item.get("traceList") or []
                eventos = []
                for t in traces:
                    eventos.append({
                        "data":   t.get("scanDate", ""),
                        "status": t.get("scanDesc", ""),
                        "local":  t.get("scanAddr", ""),
                    })
                if eventos:
                    return eventos
    except Exception:
        pass
    return []


def status_legivel(order):
    shipping = ENVIO_MAP.get(order.get("shipping_status") or "", "")
    if shipping:
        return shipping
    pagamento = PAGAMENTO_MAP.get(order.get("payment_status") or "", "")
    if pagamento:
        return pagamento
    return STATUS_MAP.get(order.get("status") or "", "Em processamento")


@app.route("/rastrear")
def rastrear():
    cpf_raw = request.args.get("cpf", "").strip()
    cpf     = re.sub(r"\D", "", cpf_raw)

    if len(cpf) != 11:
        return jsonify({"erro": "CPF invalido. Digite os 11 digitos."}), 400

    pedidos = buscar_pedidos_cpf(cpf)
    if not pedidos:
        return jsonify({"erro": "Nenhum pedido encontrado para este CPF nos ultimos 90 dias."}), 404

    resultado = []
    for p in pedidos[:5]:
        rastreio = (p.get("shipping_tracking_number") or "").strip()
        addr     = p.get("shipping_address") or {}
        eventos  = buscar_eventos_jt(rastreio) if rastreio else []
        resultado.append({
            "numero":   p.get("number"),
            "data":     (p.get("created_at") or "")[:10],
            "status":   status_legivel(p),
            "rastreio": rastreio,
            "cidade":   addr.get("city", ""),
            "eventos":  eventos,
        })

    return jsonify({"pedidos": resultado})


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/formulario")
def formulario():
    resp = Response(render_template("formulario.html"))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "ALLOWALL"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
