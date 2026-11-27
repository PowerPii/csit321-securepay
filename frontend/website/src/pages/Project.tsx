import { Box, Link, Typography } from "@mui/material";
import { PageHero, Section, SectionHeading } from "../components/Section";
import { usePageTitle } from "../components/usePageTitle";
import { brand } from "../theme";

/** Course milestones (all 9:00 pm Singapore time). Update the status when a milestone is reached. */
const MILESTONES = [
  { id: "M1", date: "5 Nov 2026", what: "User Requirement Specification, initial version", status: "Submitted" },
  { id: "M2", date: "21 Nov 2026", what: "User Requirement Specification, final version", status: "Submitted" },
  { id: "M3", date: "12 Dec 2026", what: "Preliminary technical documentation, user manual, peer assessment and slides", status: "In progress" },
  { id: "M4", date: "19 Dec 2026", what: "Progress report and prototype demonstration", status: "Planned" },
  { id: "M5", date: "13 Feb 2027", what: "Preliminary product, draft manuals, source code, video and slides", status: "Planned" },
  { id: "M6", date: "20 Feb 2027", what: "Final manuals, peer assessment, slides and reflective diaries", status: "Planned" },
];

/** Team meetings with minutes. Full minutes are in Appendix B of our Preliminary Technical Documentation. */
const MEETINGS = [
  { date: "8 Oct 2026", title: "Meeting 1: Project kick-off" },
  { date: "17 Oct 2026", title: "Meeting 2: User stories and scope" },
  { date: "1 Nov 2026", title: "Meeting 3: Roles and remaining requirements work" },
  { date: "5 Nov 2026", title: "Meeting 4: Assessment 1 drafts" },
  { date: "26 Nov 2026", title: "Meeting 5: M3 submission plan" },
];

function Rows({ rows }: { rows: { k: string; v: string; s?: string }[] }) {
  return (
    <Box>
      {rows.map((r, i) => (
        <Box
          key={r.k + r.v}
          sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "150px 1fr 120px" }, gap: 1, py: 2, borderTop: i ? `1px solid ${brand.line}` : "none" }}
        >
          <Typography sx={{ color: "text.secondary" }}>{r.k}</Typography>
          <Typography sx={{ fontWeight: 500 }}>{r.v}</Typography>
          <Typography sx={{ color: "text.secondary" }}>{r.s ?? ""}</Typography>
        </Box>
      ))}
    </Box>
  );
}

export default function Project() {
  usePageTitle("Project progress");
  return (
    <>
      <PageHero
        overline="Project progress · FYP-26-S4-36"
        title="Where our final-year project stands"
        lead="Our schedule, course milestones and meeting record for CSIT321. Last updated 27 November 2026 (week 8 of 20)."
      />

      <Section>
        <SectionHeading overline="Milestones" title="Course milestones" />
        <Rows rows={MILESTONES.map((m) => ({ k: `${m.id} · ${m.date}`, v: m.what, s: m.status }))} />
      </Section>

      <Section tone="white" id="schedule">
        <SectionHeading
          overline="Schedule"
          title="Our plan by work package"
          lead="The plan from our User Requirement Specification (week 1 starts 5 October 2026). We work in short sprints with the riskiest parts first. The build finished on time in weeks 5 to 7, so testing started early."
        />
        <Box sx={{ overflowX: "auto", border: `1px solid ${brand.line}`, borderRadius: 2, bgcolor: "#fff" }}>
          <Box
            component="img"
            src="/project/gantt.png"
            alt="Gantt chart of work packages 1 to 12 over weeks 1 to 20, with milestones M1 to M6 marked in weeks 5, 7, 10, 11, 19 and 20"
            sx={{ display: "block", width: "100%", minWidth: 760, height: "auto" }}
          />
        </Box>
        <Typography sx={{ mt: 1.5, color: "text.secondary" }}>
          <Link href="/project/gantt.png" underline="always">Open the chart at full size</Link>
        </Typography>
      </Section>

      <Section id="meetings">
        <SectionHeading
          overline="Meetings"
          title="Meeting record"
          lead="The full minutes, with attendees and action items, are in Appendix B of our Preliminary Technical Documentation."
        />
        <Rows rows={MEETINGS.map((m) => ({ k: m.date, v: m.title }))} />
      </Section>

      <Section tone="white" id="diaries">
        <SectionHeading
          overline="Reflective diaries"
          title="Individual reflective diaries"
          lead="Each member's weekly reflective diary is personal, so we share the diaries with our supervisor and assessor privately rather than on this public page."
        />
      </Section>
    </>
  );
}
