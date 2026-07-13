import { Fragment, useEffect, useState } from "react";
import { api, type LogEvent } from "@/lib/api";
import { useLogFeed } from "@/hooks/use-log-feed";
import { ConnectionLamp } from "@/components/observe/connection-lamp";
import { LogRetentionDialog } from "@/components/log-retention-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Search,
  Settings2,
} from "lucide-react";

interface LogConsolePageProps {
  onBack: () => void;
}

/** Log contexts the console can browse (must match logbook.ALL_CONTEXTS). */
const CONTEXTS = [
  { value: "auth", label: "Authentication" },
  { value: "deploy", label: "Deployments" },
  { value: "scan", label: "Registry scans" },
  { value: "backup", label: "Backup & restore" },
  { value: "admin", label: "Administration" },
  { value: "system", label: "System" },
] as const;

const OUTCOMES = [
  { value: "", label: "All outcomes" },
  { value: "success", label: "Success" },
  { value: "failure", label: "Failure" },
  { value: "denied", label: "Denied" },
  { value: "info", label: "Info" },
] as const;

const OUTCOME_PILL_COLORS: Record<string, string> = {
  success: "bg-green-500/15 text-green-700 dark:text-green-300",
  failure: "bg-red-500/15 text-red-700 dark:text-red-300",
  denied: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  info: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
};

function outcomePill(outcome: string) {
  const cls = OUTCOME_PILL_COLORS[outcome] ?? "bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-xs font-mono ${cls}`}>
      {outcome}
    </span>
  );
}

function eventPill(eventType: string) {
  return (
    <span className="inline-flex rounded bg-muted px-2 py-0.5 text-xs font-mono text-foreground">
      {eventType}
    </span>
  );
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString();
}

function DetailRow({ event }: { event: LogEvent }) {
  return (
    <tr className="border-t bg-muted/20">
      <td colSpan={7} className="px-4 py-3">
        <div className="grid gap-2 font-mono text-xs text-muted-foreground">
          {event.user_agent && (
            <div>
              <span className="font-semibold text-foreground">User agent:</span>{" "}
              {event.user_agent}
            </div>
          )}
          {event.actor_id && (
            <div>
              <span className="font-semibold text-foreground">Actor ID:</span> {event.actor_id}
            </div>
          )}
          {event.detail && (
            <pre className="overflow-x-auto rounded bg-muted/50 p-2">
              {JSON.stringify(event.detail, null, 2)}
            </pre>
          )}
          {!event.user_agent && !event.actor_id && !event.detail && (
            <span className="italic">No additional detail.</span>
          )}
        </div>
      </td>
    </tr>
  );
}

export function LogConsolePage({ onBack }: LogConsolePageProps) {
  const [context, setContext] = useState<string>("auth");
  const [eventType, setEventType] = useState<string>("");
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [outcome, setOutcome] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [query, setQuery] = useState<string>("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [retentionOpen, setRetentionOpen] = useState(false);

  // Debounce the free-text search so we don't restart the stream per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 400);
    return () => clearTimeout(t);
  }, [search]);

  // The event filter offers whatever types this context has actually seen.
  useEffect(() => {
    setEventType("");
    let cancelled = false;
    api
      .getLogEventTypes(context)
      .then((res) => {
        if (!cancelled) setEventTypes(res.event_types);
      })
      .catch(() => setEventTypes([]));
    return () => {
      cancelled = true;
    };
  }, [context]);

  const filters = {
    context,
    q: query || undefined,
    event_type: eventType || undefined,
    outcome: outcome || undefined,
  };
  const {
    events, state, paused, buffered, hasMore, loadingOlder,
    pause, resume, reconnect, loadOlder,
  } = useLogFeed(filters);

  async function handleExport(format: "csv" | "json") {
    setExporting(true);
    setExportError(null);
    try {
      const { blob, filename } = await api.exportLogs({ ...filters, format });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 h-4 w-4" /> Back
        </Button>
        <h2 className="text-lg font-semibold">Logs</h2>
        <ConnectionLamp state={state} paused={paused} />
        <span className="text-sm text-muted-foreground">
          {events.length} event{events.length === 1 ? "" : "s"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {paused ? (
            <Button size="sm" variant="outline" onClick={resume}>
              <Play className="mr-1 h-3 w-3" />
              Resume{buffered > 0 ? ` (${buffered})` : ""}
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={pause}>
              <Pause className="mr-1 h-3 w-3" />
              Pause
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={reconnect}>
            <RefreshCw className="mr-1 h-3 w-3" />
            Reconnect
          </Button>
          <Button size="sm" variant="outline" onClick={() => setRetentionOpen(true)}>
            <Settings2 className="mr-1 h-3 w-3" />
            Retention
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" disabled={exporting}>
                {exporting ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <Download className="mr-1 h-3 w-3" />
                )}
                Export
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => void handleExport("csv")}>
                Download CSV
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void handleExport("json")}>
                Download JSON
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-md border p-3 bg-muted/30">
        <div className="grid gap-1">
          <Label className="text-xs">Context</Label>
          <Select value={context} onValueChange={setContext}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CONTEXTS.map((c) => (
                <SelectItem key={c.value} value={c.value}>
                  {c.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <Label className="text-xs">Event</Label>
          <Select
            value={eventType || "all"}
            onValueChange={(v) => setEventType(v === "all" ? "" : v)}
          >
            <SelectTrigger className="w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All events</SelectItem>
              {eventTypes.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <Label className="text-xs">Outcome</Label>
          <Select
            value={outcome || "all"}
            onValueChange={(v) => setOutcome(v === "all" ? "" : v)}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OUTCOMES.map((o) => (
                <SelectItem key={o.value || "all"} value={o.value || "all"}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid flex-1 gap-1 min-w-[220px]">
          <Label className="text-xs">Search</Label>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              placeholder="Email, IP, message…"
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8"
            />
          </div>
        </div>
      </div>

      {exportError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {exportError}
        </div>
      )}

      <div className="rounded-md border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="w-8 px-2 py-2" />
              <th className="px-3 py-2 text-left">When</th>
              <th className="px-3 py-2 text-left">Event</th>
              <th className="px-3 py-2 text-left">Outcome</th>
              <th className="px-3 py-2 text-left">User</th>
              <th className="px-3 py-2 text-left">IP</th>
              <th className="px-3 py-2 text-left">Message</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-muted-foreground italic">
                  {state === "connecting"
                    ? "Loading…"
                    : "No log events match the current filters."}
                </td>
              </tr>
            )}
            {events.map((e) => (
              <Fragment key={e.id}>
                <tr
                  className="cursor-pointer border-t hover:bg-muted/30"
                  onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                >
                  <td className="px-2 py-2 text-muted-foreground">
                    {expanded === e.id ? (
                      <ChevronDown className="h-3.5 w-3.5" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" />
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">
                    {formatWhen(e.ts)}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{eventPill(e.event_type)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{outcomePill(e.outcome)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{e.actor_email ?? "—"}</td>
                  <td className="px-3 py-2 whitespace-nowrap font-mono text-xs">
                    {e.ip ?? "—"}
                  </td>
                  <td className="max-w-[400px] truncate px-3 py-2 text-muted-foreground">
                    {e.message ?? ""}
                  </td>
                </tr>
                {expanded === e.id && <DetailRow event={e} />}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {hasMore && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => void loadOlder()} disabled={loadingOlder}>
            {loadingOlder ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            Load older
          </Button>
        </div>
      )}

      <LogRetentionDialog open={retentionOpen} onOpenChange={setRetentionOpen} />
    </div>
  );
}
