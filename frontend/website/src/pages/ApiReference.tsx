import { Box, Button, Link, Typography } from "@mui/material";
import Grid from "@mui/material/Grid2";
import ArrowBackRoundedIcon from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRoundedIcon from "@mui/icons-material/ArrowForwardRounded";
import DownloadRoundedIcon from "@mui/icons-material/DownloadRounded";
import { Link as RouterLink } from "react-router-dom";
import { CodeBlock } from "../components/CodeBlock";
import { Callout, DocSection, DOCS_NAV, DocsShell, FieldList, Inline, MethodBadge, P, SimpleTable } from "../components/Docs";
import { PageHero } from "../components/Section";
import { usePageTitle } from "../components/usePageTitle";
import { ENDPOINTS, ERROR_EXAMPLE, ERRORS, EVENT_FIELDS, EVENTS, PAYMENT_FIELDS, REFUND_FIELDS, type Endpoint } from "../content/apiReference";
import { API_BASE, OPENAPI_YAML, REGISTER_URL } from "../content/site";
import { brand, fonts } from "../theme";

const ERROR_HTTP = Object.fromEntries(ERRORS.map((e) => [e.code, e.http]));

function Label({ children }: { children: string }) {
  return <Typography sx={{ fontSize: 12.5, fontWeight: 600, color: "text.secondary", mb: 1, mt: 3 }}>{children}</Typography>;
}

function EndpointSection({ e }: { e: Endpoint }) {
  return (
    <DocSection id={e.id} title={e.title}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.25, flexWrap: "wrap", mb: 2.5, p: 1.25, pl: 1.5, borderRadius: 2, bgcolor: "#fff", border: `1px solid ${brand.line}` }}>
        <MethodBadge method={e.method} />
        <Typography component="code" sx={{ fontFamily: fonts.mono, fontSize: 14, wordBreak: "break-all" }}>
          {e.path}
        </Typography>
      </Box>
      {e.summary.map((s) => (
        <P key={s}>{s}</P>
      ))}

      <Grid container spacing={{ xs: 0, lg: 4 }}>
        <Grid size={{ xs: 12, lg: 6 }} sx={{ minWidth: 0 }}>
          {e.pathParams && (
            <>
              <Label>Path parameters</Label>
              <FieldList compact fields={e.pathParams} />
            </>
          )}
          {e.method === "POST" && (
            <>
              <Label>Headers</Label>
              <FieldList
                compact
                fields={[
                  { name: "Authorization", type: "string", required: true, text: "`Bearer sp_test_…` or `Bearer sp_live_…`" },
                  { name: "Idempotency-Key", type: "string", required: true, text: "8 to 255 characters, new for each operation. See Idempotency in the guides." },
                ]}
              />
            </>
          )}
          {e.body && (
            <>
              <Label>Body</Label>
              <FieldList compact fields={e.body} />
            </>
          )}
          <Label>Replies</Label>
          <SimpleTable head={["Status", "Body"]} rows={e.replies} />
          {e.errors.length > 0 && (
            <>
              <Label>Errors</Label>
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mb: 3 }}>
                {e.errors.map((c) => (
                  <Link
                    key={c}
                    component={RouterLink}
                    to={`/developers/api#err-${c}`}
                    underline="none"
                    sx={{ fontFamily: fonts.mono, fontSize: 12.5, px: 1, py: 0.35, borderRadius: 1.5, bgcolor: "#fff", border: `1px solid ${brand.line}`, color: "text.primary", "&:hover": { borderColor: brand.navy } }}
                  >
                    <Box component="span" sx={{ color: "text.secondary", mr: 0.75 }}>
                      {ERROR_HTTP[c]}
                    </Box>
                    {c}
                  </Link>
                ))}
              </Box>
            </>
          )}
        </Grid>
        <Grid size={{ xs: 12, lg: 6 }} sx={{ minWidth: 0 }}>
          <Label>Request</Label>
          <CodeBlock snippets={e.request} />
          <Label>Response</Label>
          <Box sx={{ "& pre": { maxHeight: 480 } }}>
            <CodeBlock snippets={e.response} />
          </Box>
        </Grid>
      </Grid>
    </DocSection>
  );
}

export default function ApiReference() {
  usePageTitle("API reference");
  return (
    <>
      <PageHero
        overline="API reference"
        title="Merchant API v1"
        lead="Eleven endpoints, twelve webhook events. Everything here follows the written API contract; the OpenAPI file has the same detail in machine-readable form, and the Node.js SDK wraps it."
      >
        <Box sx={{ mt: 4, display: "flex", gap: 1.5, flexWrap: "wrap" }}>
          <Button component={RouterLink} to="/developers" variant="contained" color="secondary" size="large" startIcon={<ArrowBackRoundedIcon />}>
            Guides
          </Button>
          <Button component={RouterLink} to="/developers/api/explorer" size="large" endIcon={<ArrowForwardRoundedIcon />} sx={{ color: brand.navy }}>
            Interactive reference
          </Button>
          <Button href={OPENAPI_YAML} download size="large" startIcon={<DownloadRoundedIcon />} sx={{ color: brand.navy }}>
            OpenAPI file
          </Button>
        </Box>
      </PageHero>

      <DocsShell nav={DOCS_NAV}>
        <DocSection id="basics" title="Basics">
          <Box sx={{ p: 2, mb: 3, borderRadius: 3, bgcolor: "#fff", border: `1px solid ${brand.line}`, maxWidth: 760 }}>
            <Typography sx={{ fontSize: 12.5, fontWeight: 600, color: "text.secondary", mb: 0.5 }}>Base URL</Typography>
            <Typography sx={{ fontFamily: fonts.mono, fontSize: 14.5, wordBreak: "break-all" }}>{API_BASE}</Typography>
          </Box>
          <SimpleTable
            head={["Rule", "Value"]}
            monoCols={[]}
            rows={[
              ["Transport", "HTTPS only, TLS 1.2 or higher. Plain HTTP is refused."],
              ["Format", "UTF-8 JSON with `Content-Type: application/json`; field names in `snake_case`."],
              ["Money", "Integer cents in `SGD`. A payment is 50 to 1,000,000 cents (S$0.50 to S$10,000)."],
              ["Time", "RFC 3339 in UTC, for example `2026-11-13T04:30:00Z`."],
              ["IDs", "Prefixed: `pay_`, `rfd_`, `evt_`, `tok_`, `req_`. Customers see `SP-XXXX-XXXX` and `RF-XXXX-XXXX`."],
              ["Auth", "`Authorization: Bearer sp_test_…` on every call. Get a key from the dashboard once your account is approved."],
              ["Modes", "A test key (`sp_test_…`) and a live key (`sp_live_…`) each see only their own payments, refunds, payouts and balance. Every object has `livemode`. Live is still simulated money in this demo."],
              ["Idempotency", "`Idempotency-Key` on every `POST`, kept for 24 hours."],
              ["Signatures", "Every reply to a valid key carries `SecurePay-Signature`; so does every webhook."],
              ["Rate limit", "100 requests a minute per key, then `429` with `Retry-After`."],
              ["Versioning", "`/v1` may gain fields, endpoints, error codes and event types. Ignore what you don’t know."],
              ["Card data", "No endpoint accepts or returns a card number. A body that contains one is refused with `400 card_data_not_allowed`."],
            ]}
          />
          <P>
            <Inline text="No account yet? " />
            <Link href={REGISTER_URL} target="_blank" rel="noopener" underline="always">
              Register a merchant account
            </Link>
            <Inline text=" on the live demo gateway. All payments there are simulated." />
          </P>
        </DocSection>

        {ENDPOINTS.map((e) => (
          <EndpointSection key={e.id} e={e} />
        ))}

        <DocSection id="payment-object" title="The Payment object">
          <P>Returned by every payment endpoint and sent as `data.object` in `payment.*` events. Fields marked `| null` are always present, set to `null` when they don’t apply.</P>
          <FieldList fields={PAYMENT_FIELDS} />
        </DocSection>

        <DocSection id="refund-object" title="The Refund object">
          <P>Returned by the refund endpoints and sent as `data.object` in `refund.*` events.</P>
          <FieldList fields={REFUND_FIELDS} />
        </DocSection>

        <DocSection id="events" title="Webhook events">
          <P>
            Each event is POSTed to your approved address as JSON, signed with `SecurePay-Signature`. `created` and `screening` are not sent: you made the payment, and screening
            takes milliseconds.
          </P>
          <SimpleTable head={["Event", "data.object", "Sent when"]} monoCols={[0]} rows={EVENTS.map((e) => [e.type, e.object, e.when])} />
          <Typography sx={{ fontSize: 12.5, fontWeight: 600, color: "text.secondary", mb: 1 }}>Event envelope</Typography>
          <FieldList fields={EVENT_FIELDS} />
          <Callout tone="info">
            Delivery, headers, retries and signature checks are in the Webhooks guide. A `payment.authorised` webhook is the signal to fulfil, after you verify it.
          </Callout>
        </DocSection>

        <DocSection id="error-codes" title="Error codes">
          <P>Every error uses the same body. `type` groups the codes, `param` names the field when there is one, and `request_id` matches the `SecurePay-Request-Id` header.</P>
          <Box sx={{ mb: 3, maxWidth: 640 }}>
            <CodeBlock snippets={ERROR_EXAMPLE} />
          </Box>
          <Box sx={{ bgcolor: "#fff", border: `1px solid ${brand.line}`, borderRadius: 3, overflow: "hidden" }}>
            {ERRORS.map((er, i) => (
              <Box
                key={er.code}
                id={`err-${er.code}`}
                sx={{
                  scrollMarginTop: 110,
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "56px 300px 1fr" },
                  gap: { xs: 0.25, sm: 2 },
                  px: 2,
                  py: 1.25,
                  borderTop: i ? `1px solid ${brand.line}` : 0,
                  "&:target": { bgcolor: "#F6EFE0" },
                }}
              >
                <Typography sx={{ fontFamily: fonts.mono, fontSize: 13.5 }}>{er.http}</Typography>
                <Box>
                  <Typography sx={{ fontFamily: fonts.mono, fontSize: 13.5, fontWeight: 600, wordBreak: "break-word" }}>{er.code}</Typography>
                  <Typography sx={{ fontFamily: fonts.mono, fontSize: 12, color: "text.secondary" }}>{er.type}</Typography>
                </Box>
                <Typography sx={{ color: "text.secondary", fontSize: 14.5, lineHeight: 1.6 }}>
                  <Inline text={er.when} />
                </Typography>
              </Box>
            ))}
          </Box>
        </DocSection>
      </DocsShell>
    </>
  );
}
