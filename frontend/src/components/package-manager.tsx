import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PyPIPackageInfo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Package, Search, X, Loader2 } from "lucide-react";

interface PackageManagerProps {
  serverName: string;
  packages: string[];
  onUpdated: () => void;
}

export function PackageManager({
  serverName,
  packages,
  onUpdated,
}: PackageManagerProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PyPIPackageInfo[]>([]);
  const [searching, setSearching] = useState(false);
  const [noResults, setNoResults] = useState(false);
  const [saving, setSaving] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const search = useCallback(async (q: string) => {
    if (q.trim().length < 2) {
      setResults([]);
      setNoResults(false);
      return;
    }
    setSearching(true);
    setNoResults(false);
    try {
      const data = await api.searchPyPI(q.trim());
      setResults(data);
      setNoResults(data.length === 0);
    } catch {
      setResults([]);
      setNoResults(true);
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (query.trim().length < 2) {
      setResults([]);
      setNoResults(false);
      return;
    }
    debounceRef.current = setTimeout(() => search(query), 400);
    return () => clearTimeout(debounceRef.current);
  }, [query, search]);

  async function addPackage(name: string) {
    if (packages.includes(name)) return;
    setSaving(true);
    try {
      await api.updatePipPackages(serverName, [...packages, name]);
      setQuery("");
      setResults([]);
      onUpdated();
    } finally {
      setSaving(false);
    }
  }

  async function removePackage(name: string) {
    setSaving(true);
    try {
      await api.updatePipPackages(
        serverName,
        packages.filter((p) => p !== name)
      );
      onUpdated();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Package className="h-4 w-4 text-muted-foreground" />
        <Label className="text-sm font-medium">PyPI Packages</Label>
      </div>

      <div className="relative">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder="Search PyPI packages..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {searching && (
          <Loader2 className="absolute right-2.5 top-2.5 h-4 w-4 animate-spin text-muted-foreground" />
        )}
      </div>

      {results.length > 0 && (
        <div className="rounded-md border divide-y max-h-[240px] overflow-y-auto">
          {results.map((pkg) => {
            const alreadyAdded = packages.includes(pkg.name);
            return (
              <div
                key={pkg.name}
                className="flex items-start justify-between gap-3 p-2.5"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{pkg.name}</span>
                    <Badge variant="outline" className="text-xs">
                      {pkg.version}
                    </Badge>
                  </div>
                  {pkg.summary && (
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                      {pkg.summary}
                    </p>
                  )}
                </div>
                <Button
                  size="sm"
                  variant={alreadyAdded ? "outline" : "default"}
                  disabled={alreadyAdded || saving}
                  onClick={() => addPackage(pkg.name)}
                >
                  {alreadyAdded ? "Added" : "Add"}
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {noResults && query.trim().length >= 2 && (
        <p className="text-sm text-muted-foreground">
          No packages found for "{query.trim()}"
        </p>
      )}

      {packages.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {packages.map((pkg) => (
            <Badge key={pkg} variant="secondary" className="gap-1 pl-2.5 pr-1 py-1">
              {pkg}
              <button
                className="ml-1 rounded-sm hover:bg-muted p-0.5"
                onClick={() => removePackage(pkg)}
                disabled={saving}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}

      {packages.length === 0 && results.length === 0 && !searching && (
        <p className="text-sm text-muted-foreground">
          No packages installed. Search PyPI above to add dependencies.
        </p>
      )}
    </div>
  );
}
