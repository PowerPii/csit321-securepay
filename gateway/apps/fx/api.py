"""GET /v1/fx_rates: the rate sheet new payments lock (docs/v2/FX.md)."""
from rest_framework.response import Response

from apps.fx import rates
from apps.merchants.api import MerchantAPIView


class RatesView(MerchantAPIView):
    def get(self, request):
        return Response(rates.sheet_to_dict(rates.current()))
