import { Box, Container, Link, Typography } from "@mui/material";
import Grid from "@mui/material/Grid2";
import { Link as RouterLink } from "react-router-dom";
import { REPO_URL } from "../content/site";
import { brand } from "../theme";
import { Logo } from "./Logo";

const COLUMNS = [
  {
    title: "Product",
    links: [
      { label: "Hosted checkout", to: "/product#checkout" },
      { label: "Card vault", to: "/product#vault" },
      { label: "Fraud screening", to: "/product#fraud" },
      { label: "Webhooks", to: "/product#webhooks" },
      { label: "Staff dashboard", to: "/product#dashboard" },
    ],
  },
  {
    title: "Developers",
    links: [
      { label: "Get started", to: "/developers#get-started" },
      { label: "Quickstart", to: "/developers#quickstart" },
      { label: "API reference", to: "/developers/api" },
      { label: "Webhook events", to: "/developers/api#events" },
      { label: "Test cards", to: "/developers#test-cards" },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "About the team", to: "/about" },
      { label: "Project progress", to: "/project" },
      { label: "Pricing", to: "/pricing" },
      { label: "Contact", to: "/contact" },
      { label: "Source on GitHub", href: REPO_URL },
    ],
  },
  {
    title: "Trust",
    links: [
      { label: "System status", to: "/status" },
      { label: "Security", to: "/security" },
      { label: "Privacy policy", to: "/privacy" },
      { label: "Terms of use", to: "/terms" },
    ],
  },
];

export function Footer() {
  return (
    <Box component="footer" sx={{ bgcolor: brand.ink, color: "rgba(255,255,255,0.7)", pt: { xs: 8, md: 10 }, pb: 5 }}>
      <Container maxWidth="lg">
        <Grid container spacing={5}>
          <Grid size={{ xs: 12, md: 4 }}>
            <Logo light />
            <Typography sx={{ mt: 2, maxWidth: 320, fontSize: 15, lineHeight: 1.6 }}>
              A payment gateway prototype with fraud screening built into every payment. Built by five students for CSIT321.
            </Typography>
          </Grid>
          {COLUMNS.map((col) => (
            <Grid key={col.title} size={{ xs: 6, sm: 3, md: 2 }}>
              <Typography sx={{ color: "#fff", fontWeight: 600, mb: 2, fontSize: 14 }}>{col.title}</Typography>
              <Box component="ul" sx={{ listStyle: "none", p: 0, m: 0, display: "grid", gap: 1.25 }}>
                {col.links.map((l) => (
                  <li key={l.label}>
                    {"href" in l && l.href ? (
                      <Link href={l.href} target="_blank" rel="noopener" underline="hover" sx={{ color: "inherit", fontSize: 14.5 }}>
                        {l.label}
                      </Link>
                    ) : (
                      <Link component={RouterLink} to={(l as { to: string }).to} underline="hover" sx={{ color: "inherit", fontSize: 14.5 }}>
                        {l.label}
                      </Link>
                    )}
                  </li>
                ))}
              </Box>
            </Grid>
          ))}
        </Grid>
        <Box sx={{ borderTop: "1px solid rgba(255,255,255,0.1)", mt: 8, pt: 4, display: "grid", gap: 1.5, fontSize: 13, lineHeight: 1.6 }}>
          <Typography sx={{ fontSize: "inherit" }}>
            SecurePay is a student project for CSIT321 at the University of Wollongong, delivered with the Singapore Institute of Management. It is not a
            licensed payment service provider, is not regulated by the Monetary Authority of Singapore, and does not accept, store or move real money or
            real card data. All payments are simulated and all card numbers are synthetic test numbers.
          </Typography>
          <Typography sx={{ fontSize: "inherit", color: "rgba(255,255,255,0.62)" }}>
            © {new Date().getFullYear()} The SecurePay project team. Product names of other companies mentioned on this site belong to their owners.
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2.5 }}>
            {[
              ["Privacy", "/privacy"],
              ["Terms", "/terms"],
              ["Status", "/status"],
            ].map(([label, to]) => (
              <Link key={to} component={RouterLink} to={to} underline="hover" sx={{ color: "rgba(255,255,255,0.75)", fontSize: "inherit" }}>
                {label}
              </Link>
            ))}
          </Box>
        </Box>
      </Container>
    </Box>
  );
}
