import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from analytics.models import Signal
from analytics.orchestrator import ensure_started, get_orchestrator
from analytics.state import STATE
from analytics.tracker import compute_stats
from upstox import registry
from upstox.client import UpstoxError
from . import payload


def _sym(request):
    orch = get_orchestrator()
    watch = getattr(orch, "watch", settings.WATCHLIST)
    s = (request.GET.get("symbol") or "").upper()
    return s if s in watch else (watch[0] if watch else None)


@ensure_csrf_cookie
def index(request):
    ensure_started()
    return render(request, "dashboard/index.html", {"mode": settings.OPTIONLAB_MODE})


@ensure_csrf_cookie
def journal(request):
    return render(request, "dashboard/journal.html", {"mode": settings.OPTIONLAB_MODE})


@ensure_csrf_cookie
def explorer(request):
    return render(request, "dashboard/explorer.html", {"enabled": settings.EXPLORER_ENABLED, "mode": settings.OPTIONLAB_MODE})


@require_GET
def api_state(request):
    return JsonResponse(payload.chain_payload(_sym(request)), json_dumps_params={"default": str})


@require_POST
def api_select(request):
    body = json.loads(request.body or "{}")
    orch = get_orchestrator()
    if orch:
        orch.select(body.get("symbol", "").upper(), body.get("expiry", ""))
    return JsonResponse({"ok": True})


@require_GET
def api_signals(request):
    qs = Signal.objects.filter(mode=STATE.mode)
    if request.GET.get("symbol"):
        qs = qs.filter(underlying=request.GET["symbol"].upper())
    if request.GET.get("strategy"):
        qs = qs.filter(strategy=request.GET["strategy"])
    if request.GET.get("active") == "1":
        qs = qs.filter(status__in=Signal.ACTIVE)
    return JsonResponse({"signals": [payload.signal_dict(s) for s in qs[:int(request.GET.get("limit", 100))]]})


@require_GET
def api_stats(request):
    return JsonResponse(compute_stats(STATE.mode))


@require_GET
def api_scenario(request, pk):
    sig = Signal.objects.filter(pk=pk).first()
    return JsonResponse(payload.scenario(sig) if sig else {"error": "not found"}, status=200 if sig else 404)


@require_GET
def api_health(request):
    orch = get_orchestrator()
    return JsonResponse({"mode": STATE.mode, "engine_alive": bool(orch and orch.is_alive()), "feed": STATE.feed, "segments": STATE.segment_status,
                         "errors": list(STATE.errors)[:10], "last_engine_run": STATE.last_engine_run, "selling_allowed": settings.ALLOW_SELLING})


@require_GET
def api_endpoints(request):
    return JsonResponse({"enabled": settings.EXPLORER_ENABLED, "endpoints": registry.visible(settings.STATIC_IP_WHITELISTED)})


@require_POST
def api_call(request):
    """Read-only proxy for the Explorer. Only GET paths from the registry; the token never leaves the server."""
    if not settings.EXPLORER_ENABLED:
        return JsonResponse({"error": "Explorer disabled (set EXPLORER_ENABLED=1)"}, status=403)
    orch = get_orchestrator()
    client = getattr(orch, "client", None)
    if client is None:
        return JsonResponse({"error": "Explorer needs live mode (UPSTOX_ANALYTICS_TOKEN)"}, status=400)
    body = json.loads(request.body or "{}")
    ep = registry.find(body.get("path", ""))
    if not ep or (ep["static_ip"] and not settings.STATIC_IP_WHITELISTED):
        return JsonResponse({"error": "endpoint not allowed"}, status=403)
    vals = body.get("params", {})
    path_args = {p["name"]: vals.get(p["name"], "") for p in ep["params"] if p["in"] == "path"}
    query = {p["name"]: vals.get(p["name"]) for p in ep["params"] if p["in"] == "query"}
    missing = [p["name"] for p in ep["params"] if p["required"] and not vals.get(p["name"])]
    if missing:
        return JsonResponse({"error": f"missing: {', '.join(missing)}"}, status=400)
    path = ep["path"]
    for p in ep["params"]:                      # optional path segments left blank are dropped
        if p["in"] == "path" and not vals.get(p["name"]):
            path = path.replace("/{" + p["name"] + "}", "")
    try:
        data = client.get(path, query, path_args, raw=True)
        return JsonResponse({"ok": True, "data": data})
    except UpstoxError as e:
        return JsonResponse({"ok": False, "status": e.status, "error": e.message}, status=200)


@require_GET
def api_profile(request):
    """Account profile. Upstox only serves this to a whitelisted static IP; with a plain analytics token we report that clearly."""
    client = getattr(get_orchestrator(), "client", None)
    token_info = _token_info()
    if client is None:
        return JsonResponse({"ok": False, "reason": "demo mode", "token": token_info})
    try:
        return JsonResponse({"ok": True, "profile": client.profile(), "token": token_info})
    except UpstoxError as e:
        hint = "Profile/account APIs need your server IP whitelisted in the Upstox developer console (static IP)." if e.status in (401, 403) else ""
        return JsonResponse({"ok": False, "status": e.status, "reason": e.message, "hint": hint, "token": token_info})


def _token_info():
    """Expiry of the analytics token, read from the JWT payload locally (no network, token itself never returned)."""
    import base64, time
    try:
        part = settings.UPSTOX_TOKEN.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return {"expires_in_days": round((claims["exp"] - time.time()) / 86400), "read_only_extended": claims.get("isExtended")}
    except Exception:
        return {}
