import { NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Spinner } from "./components/ui";
import Admin from "./pages/Admin";
import AiAccess from "./pages/AiAccess";
import ChangePassword from "./pages/ChangePassword";
import Documents from "./pages/Documents";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Matters from "./pages/Matters";
import Upload from "./pages/Upload";

function Shell() {
  const { me, can, logout } = useAuth();
  if (!me) return null;
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-on" : "");
  return (
    <>
      <a className="skip" href="#main">본문으로 건너뛰기</a>
      <header className="topbar">
        <div className="brand">법무 문서 관리</div>
        <nav aria-label="주 메뉴">
          {can("documents.upload") && <NavLink to="/upload" className={link}>업로드</NavLink>}
          <NavLink to="/jobs" className={link}>처리 현황</NavLink>
          <NavLink to="/matters" className={link}>사건</NavLink>
          <NavLink to="/documents" className={link}>문서</NavLink>
          {can("mcp.token") && <NavLink to="/ai" className={link}>AI 연결</NavLink>}
          {can("users.manage") && <NavLink to="/admin" className={link}>관리</NavLink>}
        </nav>
        <div className="who">
          <span className="who-name">{me.name}</span>
          <span className="role">{me.role}</span>
          {me.groups.map((g) => <span key={g} className="tag" title="접근 그룹">{g}</span>)}
          <NavLink to="/password" className={link}>비밀번호</NavLink>
          <button className="btn btn-ghost" onClick={() => void logout()}>로그아웃</button>
        </div>
      </header>
      <main id="main" className="content"><Outlet /></main>
    </>
  );
}

export default function App() {
  const { me, loading, can } = useAuth();
  if (loading) return <div className="center"><Spinner /></div>;
  if (!me) return <Login />;
  if (me.must_change_password) return <ChangePassword />;      // 임시 비밀번호로 로그인하면 변경 전에는 아무것도 쓸 수 없다
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to={can("documents.upload") ? "/upload" : "/jobs"} replace />} />
        <Route path="upload" element={can("documents.upload") ? <Upload /> : <Navigate to="/jobs" replace />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="matters" element={<Matters />} />
        <Route path="documents" element={<Documents />} />
        <Route path="password" element={<ChangePassword />} />
        <Route path="ai" element={can("mcp.token") ? <AiAccess /> : <Navigate to="/" replace />} />
        <Route path="admin" element={can("users.manage") ? <Admin /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
