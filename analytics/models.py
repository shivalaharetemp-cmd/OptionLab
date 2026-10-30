from django.db import models

BUY_ACTIONS = ("BUY_CE", "BUY_PE")


class ChainMetric(models.Model):
    """One row per underlying per minute — feeds IV-percentile and the PCR / max-pain history charts."""
    mode = models.CharField(max_length=8, default="live")
    underlying = models.CharField(max_length=16)
    expiry = models.CharField(max_length=10)
    ts = models.DateTimeField(db_index=True)
    spot = models.FloatField()
    atm_iv = models.FloatField(null=True)
    pcr = models.FloatField(null=True)
    max_pain = models.FloatField(null=True)
    straddle = models.FloatField(null=True)

    class Meta:
        indexes = [models.Index(fields=["underlying", "ts"])]


class Signal(models.Model):
    """A paper-tracked, BUY-only option signal. Selling is impossible by construction (see save())."""
    OPEN, T1, T2, SL, TRAIL, TIMEOUT, EOD = "OPEN", "T1_HIT", "T2", "SL", "TRAIL", "TIMEOUT", "EOD"
    STATUS = [(s, s) for s in (OPEN, T1, T2, SL, TRAIL, TIMEOUT, EOD)]
    ACTIVE = (OPEN, T1)

    mode = models.CharField(max_length=8, default="live", db_index=True)
    created_at = models.DateTimeField(db_index=True)
    strategy = models.CharField(max_length=32, db_index=True)
    underlying = models.CharField(max_length=16)
    expiry = models.CharField(max_length=10)
    action = models.CharField(max_length=8, choices=[(a, a) for a in BUY_ACTIONS])
    instrument_key = models.CharField(max_length=64)
    trading_symbol = models.CharField(max_length=64)
    strike = models.FloatField()
    lot_size = models.IntegerField(default=1)
    entry = models.FloatField()
    sl = models.FloatField()
    t1 = models.FloatField()
    t2 = models.FloatField()
    confidence = models.IntegerField()
    status = models.CharField(max_length=8, choices=STATUS, default=OPEN, db_index=True)
    t1_hit = models.BooleanField(default=False)
    peak = models.FloatField(default=0)
    trough = models.FloatField(default=0)
    exit_price = models.FloatField(null=True)
    exit_at = models.DateTimeField(null=True)
    realized_r = models.FloatField(null=True)
    reasons = models.JSONField(default=list)
    greeks = models.JSONField(default=dict)
    context = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *a, **kw):
        if self.action not in BUY_ACTIONS:   # last line of defence for the "buyer only" rule
            raise ValueError(f"Selling/unsupported action refused: {self.action}")
        super().save(*a, **kw)

    @property
    def risk_pts(self):
        return self.entry - self.sl
