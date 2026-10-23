import React, { useEffect, useRef, useState } from "react";
import {
  AlertOctagon,
  Check,
  CheckCircle2,
  Clock,
  Code2,
  Copy,
  Filter,
  Layers,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Webhook,
  X,
  Zap,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:5000";

const STATUS_BADGE = {
  processed: {
    label: "Processed",
    classes: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    dot: "bg-emerald-400",
  },
  failed: {
    label: "Failed / DLQ",
    classes: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    dot: "bg-rose-500",
  },
  pending: {
    label: "Pending",
    classes: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400 animate-pulse",
  },
};

const PROVIDER_BADGE = {
  paystack: {
    label: "Paystack",
    classes: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  },
  flutterwave: {
    label: "Flutterwave",
    classes: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  },
};

function formatTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString([], {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function App() {
  const [events, setEvents] = useState([]);
  const [metrics, setMetrics] = useState({
    total: 0,
    processed: 0,
    duplicates_blocked: 0,
    failed: 0,
  });
  const [statusFilter, setStatusFilter] = useState("all");
  const [providerFilter, setProviderFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [simProvider, setSimProvider] = useState("paystack");
  const [simMode, setSimMode] = useState("normal"); // normal | failure | duplicate
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [banner, setBanner] = useState(null);
  const [copied, setCopied] = useState(false);

  // Native <dialog closedby="any"> slide-over drawer ref (modern-web-guidance compliant)
  const drawerRef = useRef(null);

  useEffect(() => {
    const dialog = drawerRef.current;
    if (!dialog) return;

    // Fallback for browsers without declarative closedby="any" support
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      const handleBackdropClick = (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        const isDialogContent =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (isDialogContent) return;
        dialog.close();
      };
      dialog.addEventListener("click", handleBackdropClick);
      return () => dialog.removeEventListener("click", handleBackdropClick);
    }
  }, []);

  const showToast = (message, type = "success") => {
    setBanner({ message, type });
    setTimeout(() => {
      setBanner((prev) => (prev?.message === message ? null : prev));
    }, 4500);
  };

  const fetchEvents = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const params = new URLSearchParams();
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (providerFilter !== "all") params.set("provider", providerFilter);
      if (searchQuery.trim()) params.set("search", searchQuery.trim());

      const res = await fetch(`${API_BASE}/api/events?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setEvents(data.events || []);
        if (data.metrics) {
          setMetrics(data.metrics);
        }
      }
    } catch {
      if (!silent) {
        showToast(
          `Unable to reach PayGuard server at ${API_BASE}. Ensure Flask is running on port 5000.`,
          "error"
        );
      }
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
    const timer = setInterval(() => fetchEvents(true), 4000);
    return () => clearInterval(timer);
  }, [statusFilter, providerFilter, searchQuery]);

  const openDrawer = (ev) => {
    setSelectedEvent(ev);
    if (drawerRef.current && !drawerRef.current.open) {
      drawerRef.current.showModal();
    }
  };

  const closeDrawer = () => {
    if (drawerRef.current && drawerRef.current.open) {
      drawerRef.current.close();
    }
    setSelectedEvent(null);
  };

  const handleSimulateWebhook = async () => {
    setActionBusy(true);
    try {
      const payload = {
        provider: simProvider,
        event_type: "charge.success",
        amount: simProvider === "paystack" ? 45000 : 12500,
        currency: simProvider === "paystack" ? "NGN" : "KES",
        customer_email:
          simProvider === "paystack"
            ? "adaeze@fintech.ng"
            : "wanjiku@mobilemoney.ke",
        simulate_failure: simMode === "failure",
        simulate_duplicate: simMode === "duplicate",
      };

      const res = await fetch(`${API_BASE}/api/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.ok) {
        if (data.duplicate) {
          showToast(
            `Idempotency Guard: Duplicate event '${data.event?.event_id}' blocked!`,
            "warning"
          );
        } else if (data.status === "failed") {
          showToast(
            `Simulated ${simProvider.toUpperCase()} event failed downstream -> routed to Dead-Letter Queue (DLQ).`,
            "error"
          );
        } else {
          showToast(
            `Simulated ${simProvider.toUpperCase()} charge.success (${data.event?.event_id}) verified & processed!`,
            "success"
          );
        }
        // Alternate default provider on next click in normal mode so 1-click tests both gateways
        if (simMode === "normal") {
          setSimProvider((prev) =>
            prev === "paystack" ? "flutterwave" : "paystack"
          );
        }
        fetchEvents(true);
      } else {
        showToast(data.message || "Simulation request failed", "error");
      }
    } finally {
      setActionBusy(false);
    }
  };

  const handleReplayEvent = async (eventId) => {
    setActionBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/events/${eventId}/replay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (res.ok) {
        showToast(
          `Replayed event #${eventId} (retry_count: ${data.retry_count}) -> Status: ${data.status.toUpperCase()}`,
          data.status === "processed" ? "success" : "warning"
        );
        if (selectedEvent?.id === eventId) {
          setSelectedEvent(data.event);
        }
        fetchEvents(true);
      } else {
        showToast(data.message || "Replay failed", "error");
      }
    } finally {
      setActionBusy(false);
    }
  };

  const copyPayload = (payloadObj) => {
    navigator.clipboard.writeText(JSON.stringify(payloadObj, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Header with 1-Click Simulate Webhook Controls */}
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3.5 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 flex items-center justify-center shadow-lg shadow-emerald-500/20">
              <Shield className="h-5 w-5 text-slate-950 stroke-[2.4]" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold tracking-tight text-white">
                  PayGuard
                </h1>
                <span className="text-[11px] font-mono uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                  Webhook Gateway &amp; DLQ
                </span>
              </div>
              <p className="text-xs text-slate-400 hidden sm:block">
                Cryptographic Verification, Idempotency &amp; Dead-Letter Queue
                for Paystack &amp; Flutterwave
              </p>
            </div>
          </div>

          {/* 1-Click Simulate Webhook Bar */}
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Select simulated payment provider"
              value={simProvider}
              onChange={(e) => setSimProvider(e.target.value)}
              className="rounded-xl bg-slate-900 border border-slate-800 px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
            >
              <option value="paystack">Paystack (HMAC-SHA512)</option>
              <option value="flutterwave">Flutterwave (verif-hash)</option>
            </select>

            <select
              aria-label="Select simulation scenario"
              value={simMode}
              onChange={(e) => setSimMode(e.target.value)}
              className="rounded-xl bg-slate-900 border border-slate-800 px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
            >
              <option value="normal">Scenario: Valid 200 OK</option>
              <option value="duplicate">Scenario: Duplicate Event ID</option>
              <option value="failure">Scenario: Downstream 503 (DLQ)</option>
            </select>

            <button
              type="button"
              onClick={handleSimulateWebhook}
              disabled={actionBusy}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-xs px-4 py-2 shadow-md shadow-emerald-500/20 transition cursor-pointer"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              Simulate Webhook
            </button>

            <button
              type="button"
              onClick={() => fetchEvents()}
              className="p-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 transition"
              title="Refresh event log"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </header>

      {/* Toast Notification Banner */}
      {banner && (
        <div className="max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 pt-4">
          <div
            className={`flex items-center justify-between gap-3 px-4 py-3 rounded-xl border text-xs sm:text-sm ${
              banner.type === "error"
                ? "bg-rose-950/75 border-rose-500/40 text-rose-200"
                : banner.type === "warning"
                ? "bg-amber-950/75 border-amber-500/40 text-amber-200"
                : "bg-emerald-950/75 border-emerald-500/40 text-emerald-200"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <Sparkles className="h-4 w-4 shrink-0" />
              <span>{banner.message}</span>
            </div>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="text-slate-400 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className="max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6 space-y-6 flex-1">
        {/* Top Metrics Bar: Total Webhooks | Processed (Green) | Duplicate Blocked (Amber) | Failed/DLQ (Red) */}
        <section
          aria-label="Webhook Telemetry Metrics"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
        >
          {/* 1. Total Webhooks */}
          <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 flex flex-col justify-between">
            <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
              <span>Total Webhooks</span>
              <Webhook className="h-4 w-4 text-slate-400" />
            </div>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="text-3xl font-bold text-white">
                {metrics.total ?? events.length}
              </span>
              <span className="text-xs text-slate-400 font-mono">
                SQLite Store
              </span>
            </div>
          </div>

          {/* 2. Processed (Green) */}
          <div className="p-5 rounded-2xl bg-slate-900/90 border border-emerald-500/30 flex flex-col justify-between">
            <div className="flex items-center justify-between text-emerald-400 text-xs font-semibold uppercase tracking-wider">
              <span>Processed</span>
              <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            </div>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="text-3xl font-bold text-emerald-400">
                {metrics.processed ?? 0}
              </span>
              <span className="text-xs text-emerald-300/80 font-mono">
                200 OK Forwarded
              </span>
            </div>
          </div>

          {/* 3. Duplicate Blocked (Amber) */}
          <div className="p-5 rounded-2xl bg-slate-900/90 border border-amber-500/30 flex flex-col justify-between">
            <div className="flex items-center justify-between text-amber-400 text-xs font-semibold uppercase tracking-wider">
              <span>Duplicate Blocked</span>
              <Layers className="h-4 w-4 text-amber-400" />
            </div>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="text-3xl font-bold text-amber-400">
                {metrics.duplicates_blocked ?? 0}
              </span>
              <span className="text-xs text-amber-300/80 font-mono">
                Idempotency Guard
              </span>
            </div>
          </div>

          {/* 4. Failed / DLQ (Red) */}
          <div className="p-5 rounded-2xl bg-slate-900/90 border border-rose-500/30 flex flex-col justify-between">
            <div className="flex items-center justify-between text-rose-400 text-xs font-semibold uppercase tracking-wider">
              <span>Failed / DLQ</span>
              <AlertOctagon className="h-4 w-4 text-rose-400" />
            </div>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="text-3xl font-bold text-rose-400">
                {metrics.failed ?? 0}
              </span>
              <span className="text-xs text-rose-300/80 font-mono">
                Ready for Replay
              </span>
            </div>
          </div>
        </section>

        {/* Event Log Table */}
        <section className="rounded-2xl bg-slate-900/90 border border-slate-800 overflow-hidden">
          {/* Table Header & Filter Bar */}
          <div className="p-4 border-b border-slate-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { id: "all", label: "All Webhooks" },
                { id: "processed", label: "Processed" },
                { id: "failed", label: "Failed / DLQ" },
                { id: "pending", label: "Pending" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setStatusFilter(tab.id)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-medium transition ${
                    statusFilter === tab.id
                      ? "bg-emerald-500/20 text-emerald-200 border border-emerald-500/40"
                      : "bg-slate-950/60 text-slate-400 hover:text-slate-200 border border-slate-800"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2.5">
              <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1.5">
                <Filter className="h-3.5 w-3.5 text-slate-400" />
                <select
                  aria-label="Filter by provider"
                  value={providerFilter}
                  onChange={(e) => setProviderFilter(e.target.value)}
                  className="bg-transparent text-xs text-slate-200 focus:outline-none"
                >
                  <option value="all" className="bg-slate-900">
                    All Providers
                  </option>
                  <option value="paystack" className="bg-slate-900">
                    Paystack
                  </option>
                  <option value="flutterwave" className="bg-slate-900">
                    Flutterwave
                  </option>
                </select>
              </div>

              <div className="relative flex-1 sm:w-64">
                <Search className="h-3.5 w-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search Event ID or JSON..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full rounded-xl bg-slate-950 border border-slate-800 pl-8 pr-3 py-1.5 text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-emerald-500"
                />
              </div>
            </div>
          </div>

          {/* Event Log Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-800/80 text-[11px] font-semibold uppercase tracking-wider text-slate-400 bg-slate-950/40">
                  <th className="py-3.5 px-4">Provider</th>
                  <th className="py-3.5 px-4">Event ID</th>
                  <th className="py-3.5 px-4">Event Type</th>
                  <th className="py-3.5 px-4">Status</th>
                  <th className="py-3.5 px-4">Retries</th>
                  <th className="py-3.5 px-4">Timestamp</th>
                  <th className="py-3.5 px-4 text-right">
                    Inspect JSON / Replay
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-xs">
                {events.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-slate-400">
                      No webhook events logged yet. Click{" "}
                      <span className="text-emerald-300 font-semibold">
                        Simulate Webhook
                      </span>{" "}
                      in the top bar to generate a Paystack or Flutterwave event.
                    </td>
                  </tr>
                ) : (
                  events.map((ev) => {
                    const st = STATUS_BADGE[ev.status] || STATUS_BADGE.pending;
                    const prov = PROVIDER_BADGE[ev.provider] || {
                      label: ev.provider,
                      classes: "bg-slate-800 text-slate-300 border-slate-700",
                    };
                    return (
                      <tr
                        key={ev.id}
                        className="hover:bg-slate-800/40 transition"
                      >
                        {/* Provider Badge */}
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-[11px] font-semibold uppercase border ${prov.classes}`}
                          >
                            {prov.label}
                          </span>
                        </td>

                        {/* Event ID */}
                        <td className="py-3.5 px-4 font-mono text-slate-200 whitespace-nowrap">
                          <div className="font-medium">{ev.event_id}</div>
                          <div className="text-[11px] text-slate-500">
                            {ev.customer_email || `Row #${ev.id}`}
                          </div>
                        </td>

                        {/* Event Type */}
                        <td className="py-3.5 px-4 font-mono text-slate-300 whitespace-nowrap">
                          {ev.event_type}
                        </td>

                        {/* Status Badge */}
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${st.classes}`}
                          >
                            <span
                              className={`h-1.5 w-1.5 rounded-full ${st.dot}`}
                            />
                            {st.label}
                          </span>
                        </td>

                        {/* Retries */}
                        <td className="py-3.5 px-4 font-mono text-slate-300 whitespace-nowrap">
                          {ev.retry_count}
                        </td>

                        {/* Timestamp */}
                        <td className="py-3.5 px-4 font-mono text-[11px] text-slate-400 whitespace-nowrap">
                          {formatTimestamp(ev.created_at)}
                        </td>

                        {/* Inspect JSON / Replay Actions */}
                        <td className="py-3.5 px-4 whitespace-nowrap text-right space-x-2">
                          <button
                            type="button"
                            onClick={() => openDrawer(ev)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition"
                          >
                            <Code2 className="h-3.5 w-3.5 text-cyan-400" />
                            Inspect JSON / Replay
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* Slide-Over "Inspect JSON / Replay" Drawer (<dialog closedby="any">) */}
      <dialog
        ref={drawerRef}
        closedby="any"
        aria-labelledby="drawer-title"
        onClose={() => setSelectedEvent(null)}
        className="fixed inset-y-0 right-0 left-auto m-0 h-screen max-h-screen w-full max-w-xl bg-slate-900 border-l border-slate-800 text-slate-100 p-0 shadow-2xl overflow-hidden"
      >
        {selectedEvent && (
          <div className="flex flex-col h-full">
            {/* Drawer Header */}
            <div className="px-6 py-5 border-b border-slate-800 bg-slate-950/70 flex items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase border ${
                      PROVIDER_BADGE[selectedEvent.provider]?.classes ||
                      "bg-slate-800 text-slate-300 border-slate-700"
                    }`}
                  >
                    {selectedEvent.provider}
                  </span>
                  <h2
                    id="drawer-title"
                    className="text-sm font-bold font-mono text-white"
                  >
                    {selectedEvent.event_id}
                  </h2>
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  Received {formatTimestamp(selectedEvent.created_at)} &bull;{" "}
                  Retry Count: {selectedEvent.retry_count}
                </p>
              </div>

              <button
                type="button"
                onClick={closeDrawer}
                className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300"
                title="Close drawer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Drawer Body */}
            <div className="p-6 overflow-y-auto flex-1 space-y-6 text-xs">
              {/* Metadata Summary */}
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
                  <span className="text-[11px] text-slate-400 block">
                    Delivery Status
                  </span>
                  <span
                    className={`font-semibold uppercase mt-1 block ${
                      selectedEvent.status === "processed"
                        ? "text-emerald-400"
                        : selectedEvent.status === "failed"
                        ? "text-rose-400"
                        : "text-amber-400"
                    }`}
                  >
                    {selectedEvent.status}
                  </span>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
                  <span className="text-[11px] text-slate-400 block">
                    HMAC Signature
                  </span>
                  <span
                    className={`font-semibold mt-1 flex items-center gap-1 ${
                      selectedEvent.signature_valid
                        ? "text-emerald-400"
                        : "text-rose-400"
                    }`}
                  >
                    {selectedEvent.signature_valid ? (
                      <>
                        <ShieldCheck className="h-3.5 w-3.5" /> Verified
                      </>
                    ) : (
                      <>
                        <ShieldAlert className="h-3.5 w-3.5" /> Invalid
                      </>
                    )}
                  </span>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
                  <span className="text-[11px] text-slate-400 block">
                    Retry Count
                  </span>
                  <span className="font-mono font-bold text-white text-sm mt-1 block">
                    {selectedEvent.retry_count}
                  </span>
                </div>
              </div>

              {/* Replay Action Box */}
              <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-xs font-semibold text-white">
                    Manual Webhook Replay
                  </h3>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Re-dispatches this payload to the target URL and increments{" "}
                    <code className="text-emerald-300">retry_count</code>.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleReplayEvent(selectedEvent.id)}
                  disabled={actionBusy}
                  className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-xs transition cursor-pointer"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Replay Webhook
                </button>
              </div>

              {/* Raw JSON Payload Inspector */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <Terminal className="h-3.5 w-3.5 text-cyan-400" />
                    Raw Webhook JSON Payload
                  </span>
                  <button
                    type="button"
                    onClick={() => copyPayload(selectedEvent.payload)}
                    className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-white"
                  >
                    {copied ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-emerald-400" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5" />
                        Copy JSON
                      </>
                    )}
                  </button>
                </div>
                <pre className="p-4 rounded-xl bg-slate-950 border border-slate-800 font-mono text-[11px] text-emerald-300 overflow-x-auto leading-relaxed">
                  {JSON.stringify(selectedEvent.payload, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
