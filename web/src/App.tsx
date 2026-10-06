import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useMe } from "./api/hooks";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { AuditPage } from "./pages/AuditPage";
import { CasePage } from "./pages/CasePage";
import { LoginPage } from "./pages/LoginPage";
import { OpsPage } from "./pages/OpsPage";
import { ProtocolsPage } from "./pages/ProtocolsPage";
import { QueuePage } from "./pages/QueuePage";
import { UploadPage } from "./pages/UploadPage";

// The evaluation pages carry the charting library; load them only when visited.
const EvalPage = lazy(() => import("./pages/EvalPage").then((m) => ({ default: m.EvalPage })));
const EvalCasePage = lazy(() =>
  import("./pages/EvalCasePage").then((m) => ({ default: m.EvalCasePage })),
);

export function App() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <Spinner />;
  const user = me.data;
  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" state={{ from: location }} replace />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout user={user} />}>
        <Route path="/queue" element={<QueuePage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/cases/:id" element={<CasePage key={location.pathname} user={user} />} />
        <Route path="/eval" element={<Suspense fallback={<Spinner />}><EvalPage user={user} /></Suspense>} />
        <Route path="/eval/:runId/cases/:caseId" element={<Suspense fallback={<Spinner />}><EvalCasePage /></Suspense>} />
        <Route path="/protocols" element={<ProtocolsPage user={user} />} />
        {user.role === "admin" && <Route path="/audit" element={<AuditPage />} />}
        {user.role === "admin" && <Route path="/ops" element={<OpsPage />} />}
        <Route path="*" element={<Navigate to={user.role === "intake" ? "/upload" : "/queue"} replace />} />
      </Route>
    </Routes>
  );
}
