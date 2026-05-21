import { Label } from "@/components/ui/label";
import { Code } from "lucide-react";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { useTheme } from "@/hooks/use-theme";

interface ImportsEditorProps {
  imports: string[];
  onChange: (imports: string[]) => void;
}

export function ImportsEditor({ imports, onChange }: ImportsEditorProps) {
  const { resolvedTheme } = useTheme();
  const value = imports.join("\n");

  function handleChange(val: string) {
    const lines = val.split("\n").filter((l) => l.trim() !== "" || val.endsWith("\n"));
    // Keep empty trailing line if user just pressed enter
    if (val.endsWith("\n") && lines[lines.length - 1] !== "") {
      lines.push("");
    }
    onChange(val.trim() ? val.split("\n") : []);
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Code className="h-4 w-4 text-muted-foreground" />
        <Label className="text-sm font-medium">Python Imports</Label>
      </div>
      <p className="text-xs text-muted-foreground">
        Import statements and global vars available to all primitives. One per line.
      </p>
      <div className="rounded-md border overflow-hidden">
        <CodeMirror
          value={value}
          onChange={handleChange}
          theme={resolvedTheme}
          extensions={[python()]}
          placeholder={"import requests\nfrom datetime import datetime"}
          minHeight="80px"
          basicSetup={{
            lineNumbers: true,
            foldGutter: false,
            highlightActiveLine: true,
            autocompletion: false,
          }}
        />
      </div>
    </div>
  );
}
