import React, { useEffect, useState } from "react";
import { apiRequest, getStoredToken, setStoredToken } from "./api";
import { IndividualView } from "./components/IndividualView";
import { ProviderView } from "./components/ProviderView";
import type { AuthMeResponse, FreshnessStatus, ViewMode } from "./types";

export const App: React.FC = () => {
  const [me, setMe] = useState<AuthMeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("individual");
  const [freshness, setFreshness] = useState<FreshnessStatus | null>(null);
  const [toast, setToast] = useState<string>("");

  // Auth states (WF-01)
  const [authTab, setAuthTab] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [authError, setAuthError] = useState("");
  const [devVerifyToken, setDevVerifyToken] = useState("");

  // Org creation modal
  const [showOrgModal, setShowOrgModal] = useState(false);
  const [newOrgName, setNewOrgName] = useState("");
  const [newOrgCurrency, setNewOrgCurrency] = useState("USD");

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 4500);
  };

  const loadSession = async () => {
    const token = getStoredToken();
    if (!token) {
      setLoading(false);
      return;
    }
    try {
      const profile = await apiRequest<AuthMeResponse>("/api/v1/auth/me");
      setMe(profile);
    } catch {
      setStoredToken("");
      setMe(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSession();
  }, []);

  const handleLogin = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setAuthError("");
    try {
      const res = await apiRequest<{ access_token: string }>("/api/v1/auth/login", {
        method: "POST",
        body: { email, password },
      });
      setStoredToken(res.access_token);
      await loadSession();
      notify("Signed in! Your login email is saved & masked for sending and receiving.");
    } catch (err: any) {
      setAuthError(err.message || "Login failed");
    }
  };

  const handleResetAndLogin = async () => {
    if (!email || password.length < 8) {
      setAuthError("Enter your email and a new password (at least 8 characters), then click Reset Password & Sign In.");
      return;
    }
    setAuthError("");
    try {
      await apiRequest("/api/v1/auth/reset-password", {
        method: "POST",
        body: { email, new_password: password },
      });
      await handleLogin();
    } catch (err: any) {
      setAuthError(err.message || "Password reset failed");
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError("");
    try {
      const res = await apiRequest<{ verification_token?: string }>("/api/v1/auth/register", {
        method: "POST",
        body: { email, password, full_name: fullName || "SubSphere User" },
      });
      if (res.verification_token) {
        await apiRequest("/api/v1/auth/verify-email", {
          method: "POST",
          body: { token: res.verification_token },
        });
        const loginRes = await apiRequest<{ access_token: string }>("/api/v1/auth/login", {
          method: "POST",
          body: { email, password },
        });
        setStoredToken(loginRes.access_token);
        await loadSession();
        notify("Account created, email saved & masked, and signed in automatically!");
      } else {
        setDevVerifyToken("");
        notify("Free Personal account registered! Check your email to verify.");
      }
    } catch (err: any) {
      setAuthError(err.message || "Registration failed");
    }
  };

  const handleVerifyEmail = async () => {
    if (!devVerifyToken) return;
    await apiRequest("/api/v1/auth/verify-email", {
      method: "POST",
      body: { token: devVerifyToken },
    });
    setDevVerifyToken("");
    setAuthTab("login");
    notify("Email verified! Sign in to access your Individual Free workspace.");
  };

  const handleCreateOrg = async (e: React.FormEvent) => {
    e.preventDefault();
    const slug = newOrgName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const org = await apiRequest<{ id: string }>("/api/v1/organizations", {
      method: "POST",
      body: {
        name: newOrgName,
        slug: slug || `org-${Date.now()}`,
        default_currency: newOrgCurrency,
      },
    });
    await apiRequest(`/api/v1/organizations/${org.id}/switch-context`, { method: "POST" });
    setShowOrgModal(false);
    setNewOrgName("");
    await loadSession();
    setViewMode("provider");
    notify("Organization created and switched to Provider View");
  };

  const activeOrg = me?.memberships.find((m) => m.organization_id === me.active_org_id) || me?.memberships[0] || null;

  if (loading) {
    return <div className="auth-page"><p>Loading SubSphere 4.0...</p></div>;
  }

  if (!me) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="brand" style={{ marginBottom: 14 }}>
            <span className="brand-mark">S</span>
            <span>SubSphere 4.0</span>
          </div>
          <p className="subtitle" style={{ marginBottom: 18 }}>
            Subscription &amp; Usage Intelligence Platform. Individual view is permanently free with no card required.
          </p>

          <div className="view-switcher" role="tablist" aria-label="Authentication Mode" style={{ marginBottom: 16 }}>
            <button
              type="button"
              role="tab"
              aria-selected={authTab === "login"}
              className="view-tab"
              onClick={() => setAuthTab("login")}
            >
              Sign In
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={authTab === "register"}
              className="view-tab"
              onClick={() => setAuthTab("register")}
            >
              Create Free Account
            </button>
          </div>

          {authError && (
            <div className="alert alert-danger" role="alert">
              <div>{authError}</div>
              {authTab === "login" && authError.toLowerCase().includes("invalid email or password") && (
                <div style={{ marginTop: 8 }}>
                  <button type="button" className="btn btn-sm" onClick={() => void handleResetAndLogin()}>
                    Reset Password &amp; Sign In (with current Email/Password)
                  </button>
                </div>
              )}
            </div>
          )}

          {devVerifyToken && (
            <div className="alert alert-info">
              Dev Email Token: <code>{devVerifyToken}</code>{" "}
              <button type="button" className="btn btn-primary btn-sm" onClick={handleVerifyEmail}>
                Verify Now
              </button>
            </div>
          )}

          {authTab === "login" ? (
            <form onSubmit={handleLogin}>
              <div className="field" style={{ marginBottom: 12 }}>
                <label htmlFor="auth-email">Email</label>
                <input id="auth-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </div>
              <div className="field" style={{ marginBottom: 16 }}>
                <label htmlFor="auth-pass">Password</label>
                <input id="auth-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: "100%" }}>Sign In</button>
            </form>
          ) : (
            <form onSubmit={handleRegister}>
              <div className="field" style={{ marginBottom: 12 }}>
                <label htmlFor="reg-name">Full Name</label>
                <input id="reg-name" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
              </div>
              <div className="field" style={{ marginBottom: 12 }}>
                <label htmlFor="reg-email">Email</label>
                <input id="reg-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </div>
              <div className="field" style={{ marginBottom: 16 }}>
                <label htmlFor="reg-pass">Password (min 12 chars recommended)</label>
                <input id="reg-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: "100%" }}>Register Individual — Free</button>
            </form>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`app-shell ${viewMode === "provider" ? "theme-provider" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">S</span>
          <span>SubSphere 4.0</span>
        </div>

        <div className="view-switcher" role="tablist" aria-label="Product View Switcher">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "individual"}
            className="view-tab"
            onClick={() => setViewMode("individual")}
          >
            Individual — Free
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "provider"}
            className="view-tab"
            onClick={() => setViewMode("provider")}
          >
            Provider — Business
          </button>
        </div>

        <div className="topbar-controls">
          {viewMode === "provider" && (
            <>
              <select
                aria-label="Active Organization"
                value={activeOrg?.organization_id || ""}
                onChange={async (e) => {
                  const oid = e.target.value;
                  if (oid) {
                    await apiRequest(`/api/v1/organizations/${oid}/switch-context`, { method: "POST" });
                    await loadSession();
                  }
                }}
                style={{ padding: "6px 10px", borderRadius: 7, border: "1px solid #cbd5e1" }}
              >
                {me.memberships.map((m) => (
                  <option key={m.organization_id} value={m.organization_id}>
                    {m.organization_name} ({m.role})
                  </option>
                ))}
              </select>
              <button type="button" className="btn btn-sm" onClick={() => setShowOrgModal(true)}>
                + New Business Org
              </button>
            </>
          )}
          <span
            className="subtitle"
            style={{ margin: 0 }}
            title={me.user.relay_mask_email ? `Active Masked Relay: ${me.user.relay_mask_email}` : me.user.email}
          >
            {me.user.masked_email || me.user.email}
            {me.user.relay_mask_email && (
              <span className="badge" style={{ marginLeft: 6, fontSize: "0.75rem" }}>
                Mask: {me.user.relay_mask_email}
              </span>
            )}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setStoredToken("");
              try {
                window.localStorage.removeItem("subsphere_ai_permission");
              } catch {
                // ignore
              }
              setMe(null);
            }}
          >
            Sign Out
          </button>
        </div>
      </header>

      {freshness?.is_stale && freshness.banner && viewMode === "provider" && (
        <div className="stale-banner" role="status">
          <span>{freshness.banner}</span>
        </div>
      )}

      {toast && (
        <div
          role="status"
          style={{
            position: "fixed",
            bottom: 18,
            right: 18,
            background: "#0f172a",
            color: "#fff",
            padding: "10px 16px",
            borderRadius: 8,
            zIndex: 60,
            boxShadow: "0 8px 20px rgba(0,0,0,0.25)",
          }}
        >
          {toast}
        </div>
      )}

      {viewMode === "individual" ? (
        <IndividualView onNotify={notify} />
      ) : activeOrg ? (
        <ProviderView
          orgId={activeOrg.organization_id}
          orgName={activeOrg.organization_name}
          defaultCurrency={activeOrg.default_currency}
          onNotify={notify}
          onFreshnessChange={setFreshness}
        />
      ) : (
        <main className="main-content">
          <div className="card">
            <h1>Create Your Business Provider Organization</h1>
            <p className="subtitle">
              Your Individual — Free view remains completely free. Create a Business Organization to manage subscribers, metering, invoices, and reports.
            </p>
            <button type="button" className="btn btn-primary" onClick={() => setShowOrgModal(true)}>
              + Create Business Organization
            </button>
          </div>
        </main>
      )}

      {showOrgModal && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="new-org-title">
            <h2 id="new-org-title" style={{ marginTop: 0 }}>Create Business Organization</h2>
            <form onSubmit={handleCreateOrg} className="form-grid">
              <div className="field">
                <label htmlFor="org-name">Organization Name</label>
                <input id="org-name" value={newOrgName} onChange={(e) => setNewOrgName(e.target.value)} required />
              </div>
              <div className="field">
                <label htmlFor="org-cur">Default Currency</label>
                <select id="org-cur" value={newOrgCurrency} onChange={(e) => setNewOrgCurrency(e.target.value)}>
                  <option value="USD">USD</option>
                  <option value="PKR">PKR</option>
                  <option value="EUR">EUR</option>
                </select>
              </div>
              <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button type="button" className="btn" onClick={() => setShowOrgModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create Organization</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
