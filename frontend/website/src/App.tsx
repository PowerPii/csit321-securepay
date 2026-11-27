import { Box, CircularProgress } from "@mui/material";
import { lazy, Suspense, useEffect } from "react";
import { Outlet, Route, Routes, useLocation } from "react-router-dom";
import { Footer } from "./components/Footer";
import { DisclaimerBar, Header } from "./components/Header";
import Home from "./pages/Home";

const Product = lazy(() => import("./pages/Product"));
const Developers = lazy(() => import("./pages/Developers"));
const ApiReference = lazy(() => import("./pages/ApiReference"));
const ApiExplorer = lazy(() => import("./pages/ApiExplorer"));
const Security = lazy(() => import("./pages/Security"));
const Pricing = lazy(() => import("./pages/Pricing"));
const About = lazy(() => import("./pages/About"));
const Project = lazy(() => import("./pages/Project"));
const Contact = lazy(() => import("./pages/Contact"));
const Status = lazy(() => import("./pages/Status"));
const Privacy = lazy(() => import("./pages/Privacy"));
const Terms = lazy(() => import("./pages/Terms"));
const NotFound = lazy(() => import("./pages/NotFound"));

/** Scroll to the top on page change, or to the #section when the link has one. */
function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      const id = decodeURIComponent(hash.slice(1));
      let tries = 0;
      const find = () => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        else if (tries++ < 20) setTimeout(find, 50);
      };
      find();
    } else {
      window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
    }
  }, [pathname, hash]);
  return null;
}

function Layout() {
  return (
    <Box sx={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <ScrollManager />
      <DisclaimerBar />
      <Header />
      <Box component="main" sx={{ flex: 1 }}>
        <Suspense
          fallback={
            <Box sx={{ display: "grid", placeItems: "center", minHeight: "60vh" }}>
              <CircularProgress />
            </Box>
          }
        >
          <Outlet />
        </Suspense>
      </Box>
      <Footer />
    </Box>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="product" element={<Product />} />
        <Route path="developers" element={<Developers />} />
        <Route path="developers/api" element={<ApiReference />} />
        <Route path="developers/api/explorer" element={<ApiExplorer />} />
        <Route path="security" element={<Security />} />
        <Route path="pricing" element={<Pricing />} />
        <Route path="about" element={<About />} />
        <Route path="project" element={<Project />} />
        <Route path="contact" element={<Contact />} />
        <Route path="status" element={<Status />} />
        <Route path="privacy" element={<Privacy />} />
        <Route path="terms" element={<Terms />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
