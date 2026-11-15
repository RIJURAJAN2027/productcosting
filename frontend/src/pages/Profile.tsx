import { Link } from "react-router-dom";
import { useAuth } from "../context";

export default function Profile() {
  const { me, can } = useAuth();
  return (
    <>
      <div className="page-head"><h1>Profile</h1></div>
      <div className="card stack" style={{ maxWidth: 520 }}>
        <div><strong>{me?.name}</strong></div>
        <div className="muted">{me?.email}</div>
        <div className="muted small" style={{ textTransform: "capitalize" }}>Role: {me?.role}</div>
        <div className="note-box">
          {can("admin")
            ? <>Passwords for every user, including yours, are set under <Link to="/admin">Administration → Users</Link>.</>
            : <>To change your password, contact your administrator.</>}
        </div>
      </div>
    </>
  );
}
