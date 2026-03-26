import { useEffect, useState } from "react";
import { api, type Template } from "@/lib/api";

export function useTemplates() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTemplates().then(setTemplates).finally(() => setLoading(false));
  }, []);

  return { templates, loading };
}
