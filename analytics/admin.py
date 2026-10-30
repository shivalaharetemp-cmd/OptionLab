from django.contrib import admin
from .models import ChainMetric, Signal

admin.site.register(Signal, list_display=("created_at", "strategy", "underlying", "action", "strike", "entry", "confidence", "status", "realized_r"))
admin.site.register(ChainMetric)
