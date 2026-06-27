import os
import re
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

STORE_ID    = os.environ.get("NUVEMSHOP_STORE_ID", "")
TOKEN       = os.environ.get("NUVEMSHOP_ACCESS_TOKEN", "")
BASE_URL    = f"https://api.nuvemshop.com.br/v1/{STORE_ID}"
JT_VIP_BASE = "https://vip.jtjms-br.com"
JT_USER     = os.environ.get("JT_USUARIO", "")
JT_PASS     = os.environ.get("JT_SENHA", "")

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
    candidatos = [
        order.get("contact_document"),
        order.get("contact_identification"),
    ]
    customer = order.get("customer")
    if isinstance(customer, dict):
        candidatos.append(customer.get("identification"))
    billing = order.get("billing_address")
    if isinstance(billing, dict):
        candidatos.append(billing.get("document"))
    for campo in candidatos:
        if campo:
            return re.sub(r"\D", "", str(campo))
    return ""

def buscar_cliente_por_cpf(cpf):
    """Retorna (customer_id, email) buscando pelo CPF na API de clientes."""
    for q in [cpf, cpf[:3] + "." + cpf[3:6] + "." + cpf[6:9] + "-" + cpf[9:]]:
        page = 1
        while page <= 3:
            try:
                r = requests.get(
                    f"{BASE_URL}/customers",
                    headers=headers(),
                    params={"per_page": 200, "page": page, "q": q},
                    timeout=15,
                )
            except Exception:
                break
            if r.status_code != 200:
                break
            clientes = r.json()
            if not clientes:
                break
            for c in clientes:
                doc = re.sub(r"\D", "", c.get("identification") or "")
                if doc == cpf:
                    return c.get("id"), c.get("email") or ""
            if len(clientes) < 200:
                break
            page += 1
    return None, ""

def buscar_pedidos(cpf):
    customer_id, email = buscar_cliente_por_cpf(cpf)
    if not customer_id:
        return []

    # Tenta filtrar por e-mail (q= funciona para email na Nuvemshop)
    if email:
        try:
            r = requests.get(
                f"{BASE_URL}/orders",
                headers=headers(),
                params={"per_page": 10, "page": 1, "q": email,
                        "sort_by": "created_at", "sort_direction": "desc"},
                timeout=15,
            )
            if r.status_code == 200:
                pedidos = r.json()
                # Confirma que os pedidos são desse cliente
                corretos = [p for p in pedidos if str(p.get("contact_email") or "") == email
                            or extrair_cpf(p) == cpf]
                if corretos:
                    return sorted(corretos, key=lambda x: x.get("created_at", ""), reverse=True)
        except Exception:
            pass

    # Fallback: busca por CPF diretamente nos pedidos recentes
    import time
    data_min = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00-03:00")
    pedidos = []
    inicio = time.time()
    page = 1
    while page <= 20:
        if time.time() - inicio > 80:
            break
        try:
            r = requests.get(
                f"{BASE_URL}/orders",
                headers=headers(),
                params={"per_page": 200, "page": page, "created_at_min": data_min,
                        "sort_by": "created_at", "sort_direction": "desc"},
                timeout=15,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        for o in data:
            if extrair_cpf(o) == cpf or str(o.get("contact_email") or "") == email:
                pedidos.append(o)
        if len(data) < 200:
            break
        page += 1
    return sorted(pedidos, key=lambda x: x.get("created_at", ""), reverse=True)

_jt_session = requests.Session()
_jt_token   = {"value": "", "expires": 0}

def jt_login():
    import time
    if _jt_token["value"] and time.time() < _jt_token["expires"]:
        return _jt_token["value"]
    endpoints = [
        f"{JT_VIP_BASE}/api/user/login",
        f"{JT_VIP_BASE}/api/auth/login",
        f"{JT_VIP_BASE}/user/login",
    ]
    payload = {"loginName": JT_USER, "loginPwd": JT_PASS, "captchaCode": ""}
    hdrs = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
            "Referer": JT_VIP_BASE, "Origin": JT_VIP_BASE}
    for ep in endpoints:
        try:
            r = _jt_session.post(ep, json=payload, headers=hdrs, timeout=10)
            d = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
            token = (d.get("data") or {}).get("token") or d.get("token") or ""
            if token:
                _jt_token["value"]   = token
                _jt_token["expires"] = time.time() + 3600 * 6
                return token
        except Exception:
            continue
    return ""

def buscar_rastreio_jt_vip(codigo):
    token = jt_login()
    hdrs  = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    endpoints = [
        (f"{JT_VIP_BASE}/api/waybill/trace",  "POST", {"waybillNo": codigo}),
        (f"{JT_VIP_BASE}/api/track/query",    "POST", {"billCode": codigo}),
        (f"{JT_VIP_BASE}/api/order/trace",    "POST", {"waybillNo": codigo}),
        (f"{JT_VIP_BASE}/api/waybill/trace?waybillNo={codigo}", "GET", None),
    ]
    for url, method, body in endpoints:
        try:
            if method == "POST":
                r = _jt_session.post(url, json=body, headers=hdrs, timeout=10)
            else:
                r = _jt_session.get(url, headers=hdrs, timeout=10)
            if not r.headers.get("content-type","").startswith("application/json"):
                continue
            d = r.json()
            traces = (d.get("data") or {})
            if isinstance(traces, dict):
                traces = traces.get("traceList") or traces.get("traces") or traces.get("list") or []
            if isinstance(traces, list) and traces:
                return [{"data": t.get("scanDate") or t.get("time",""),
                         "status": t.get("scanDesc") or t.get("desc",""),
                         "local":  t.get("scanAddr") or t.get("addr","")} for t in traces]
        except Exception:
            continue
    return []

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
        eventos = buscar_rastreio_jt_vip(codigo) or buscar_rastreio_jt(codigo)
        return eventos, "jt"
    eventos = buscar_rastreio_jt_vip(codigo) or buscar_rastreio_jt(codigo) or buscar_rastreio_correios(codigo)
    return eventos, "desconhecida"


@app.route("/rastrear")
def rastrear():
    cpf = re.sub(r"\D", "", request.args.get("cpf", ""))
    if len(cpf) != 11:
        return jsonify({"erro": "CPF inválido."}), 400

    pedidos = buscar_pedidos(cpf)
    if not pedidos:
        return jsonify({"erro": "Nenhum pedido encontrado. Verifique se o CPF foi informado corretamente no momento da compra."}), 404

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


@app.route("/busca-cpf")
def busca_cpf():
    cpf = re.sub(r"\D", "", request.args.get("cpf", ""))
    if not cpf:
        return jsonify({"erro": "passe ?cpf=..."}), 400
    data_min = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00-03:00")
    encontrados = []
    todos_docs = []
    # Busca clientes com q=cpf e mostra os que vieram antes do filtro
    clientes_raw = []
    try:
        r = requests.get(f"{BASE_URL}/customers", headers=headers(),
            params={"per_page": 10, "page": 1, "q": cpf}, timeout=15)
        if r.status_code == 200:
            for c in r.json():
                clientes_raw.append({
                    "id": c.get("id"),
                    "nome": c.get("name"),
                    "email": (c.get("email") or "")[:8] + "***",
                    "identification": c.get("identification"),
                    "identification_limpo": re.sub(r"\D", "", c.get("identification") or ""),
                })
    except Exception as e:
        clientes_raw = [{"erro": str(e)}]

    customer_id = buscar_cliente_por_cpf(cpf)
    pedidos = []
    if customer_id:
        try:
            r2 = requests.get(f"{BASE_URL}/orders", headers=headers(),
                params={"per_page": 10, "page": 1, "customer_id": customer_id,
                        "sort_by": "created_at", "sort_direction": "desc"}, timeout=15)
            if r2.status_code == 200:
                pedidos = [{"numero": p.get("number"), "data": (p.get("created_at") or "")[:10],
                            "cpf_pedido": extrair_cpf(p)} for p in r2.json()]
        except Exception:
            pass

    return jsonify({
        "cpf_buscado": cpf,
        "customer_id_encontrado": customer_id,
        "clientes_retornados_pela_api": clientes_raw,
        "pedidos": pedidos,
    })


@app.route("/testar-jt-vip")
def testar_jt_vip():
    codigo = request.args.get("codigo", "888030793865465")
    hdrs = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
            "Referer": JT_VIP_BASE, "Origin": JT_VIP_BASE}
    resultado = {"user_configurado": bool(JT_USER), "tentativas": []}
    # tenta login
    for ep in [f"{JT_VIP_BASE}/api/user/login", f"{JT_VIP_BASE}/api/auth/login", f"{JT_VIP_BASE}/user/login"]:
        try:
            r = _jt_session.post(ep, json={"loginName": JT_USER, "loginPwd": JT_PASS}, headers=hdrs, timeout=10)
            resultado["tentativas"].append({"url": ep, "status": r.status_code,
                "content_type": r.headers.get("content-type",""), "resposta": r.text[:400]})
        except Exception as e:
            resultado["tentativas"].append({"url": ep, "erro": str(e)})
    return jsonify(resultado)


@app.route("/testar-jt")
def testar_jt():
    codigo = request.args.get("codigo", "888030793865465")
    resultado = {"codigo": codigo, "tentativas": []}

    # Endpoint 1
    try:
        r = requests.post(
            "https://www.jtexpress.com.br/ordertrack/trajectory/list",
            json={"waybillNos": [codigo]},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resultado["tentativas"].append({"endpoint": "trajectory/list", "status": r.status_code, "resposta": r.text[:500]})
    except Exception as e:
        resultado["tentativas"].append({"endpoint": "trajectory/list", "erro": str(e)})

    # Endpoint 2
    try:
        r2 = requests.get(
            f"https://www.jtexpress.com.br/index/query/getnewlist.html?waybillNo={codigo}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resultado["tentativas"].append({"endpoint": "getnewlist", "status": r2.status_code, "resposta": r2.text[:500]})
    except Exception as e:
        resultado["tentativas"].append({"endpoint": "getnewlist", "erro": str(e)})

    # Endpoint 3 - API internacional JT
    try:
        r3 = requests.post(
            "https://www.jtexpress.com.br/api/trace/getTrace",
            json={"billCodes": [codigo]},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resultado["tentativas"].append({"endpoint": "getTrace", "status": r3.status_code, "resposta": r3.text[:500]})
    except Exception as e:
        resultado["tentativas"].append({"endpoint": "getTrace", "erro": str(e)})

    return jsonify(resultado)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
