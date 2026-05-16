import { useState } from "react";
import { Hammer, FileCode, Save, Sparkles, Container, Anchor } from "lucide-react";
import { api } from "../api";
import { Card, SectionHeader } from "../components/Card";
import { Button } from "../components/Button";
import { CodeBlock } from "../components/CodeBlock";

/**
 * Programmer — three generators with live preview + save-to-file.
 * Each preview is the actual tool output (the LLM is not involved);
 * Save calls the gated write_file which surfaces an approval card.
 */
export function ProgrammerPage(): JSX.Element {
  return (
    <section className="flex flex-col min-h-0 h-full">
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-3">
        <Hammer size={14} className="text-accent-blue self-center" />
        <h1 className="font-display text-base font-semibold text-text-primary">Programmer</h1>
        <span className="text-[11px] font-mono text-text-muted">
          generate Dockerfile / compose / helm values; save through the gated write_file
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-6">
        <SectionHeader title="Generators" hint="preview, then save" />
        <DockerfileGenerator />
        <ComposeGenerator />
        <HelmGenerator />
      </div>
    </section>
  );
}

// ---------- Dockerfile ----------

function DockerfileGenerator(): JSX.Element {
  const [language, setLanguage] = useState<"python" | "node" | "go">("python");
  const [version, setVersion] = useState("3.12");
  const [cmdText, setCmdText] = useState("python app.py");
  const [out, setOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savePath, setSavePath] = useState("/tmp/Dockerfile");
  const [saving, setSaving] = useState(false);

  const generate = async (): Promise<void> => {
    setBusy(true);
    try {
      const cmd = cmdText.trim().split(/\s+/);
      const r = await api.invokeTool("programmer", "generate_dockerfile", {
        language, version, cmd,
      });
      setOut(String(r.result ?? ""));
    } catch (e) { setOut(`error: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  const save = async (): Promise<void> => {
    if (!out) return;
    if (!window.confirm(`Save Dockerfile to ${savePath}?\nApproval card will surface in the right sidebar.`)) return;
    setSaving(true);
    try {
      await api.invokeTool("programmer", "write_file", { path: savePath, content: out });
      alert("Saved.");
    } catch (e) { alert(`save failed: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-baseline gap-2">
        <Container size={14} className="text-accent-orange self-center" />
        <h3 className="font-display text-sm font-semibold text-text-primary">Dockerfile</h3>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Select
          label="language"
          value={language}
          options={["python", "node", "go"]}
          onChange={(v) => setLanguage(v as "python" | "node" | "go")}
        />
        <Field label="version" value={version} onChange={setVersion} placeholder="3.12 / 20 / 1.22" />
        <Field label="cmd (space-separated)" value={cmdText} onChange={setCmdText} placeholder="python app.py" />
      </div>
      <div className="flex gap-2">
        <Button variant="primary" icon={<Sparkles size={12} />} loading={busy} onClick={generate}>
          Generate
        </Button>
        {out && (
          <>
            <Field label="" value={savePath} onChange={setSavePath} placeholder="/tmp/Dockerfile" className="flex-1" />
            <Button variant="danger" icon={<Save size={12} />} loading={saving} onClick={save}>
              Save (gated)
            </Button>
          </>
        )}
      </div>
      {out !== null && <CodeBlock text={out} language="dockerfile" />}
    </Card>
  );
}

// ---------- Compose ----------

function ComposeGenerator(): JSX.Element {
  const [name, setName] = useState("web");
  const [image, setImage] = useState("nginx:alpine");
  const [port, setPort] = useState(8080);
  const [envText, setEnvText] = useState("");
  const [out, setOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savePath, setSavePath] = useState("/tmp/docker-compose.yml");
  const [saving, setSaving] = useState(false);

  const parseEnv = (s: string): Record<string, string> => {
    const env: Record<string, string> = {};
    s.split("\n").forEach((line) => {
      const [k, ...rest] = line.split("=");
      if (k && rest.length > 0) env[k.trim()] = rest.join("=").trim();
    });
    return env;
  };
  const generate = async (): Promise<void> => {
    setBusy(true);
    try {
      const env = parseEnv(envText);
      const r = await api.invokeTool("programmer", "generate_compose_service", {
        name, image, port, ...(Object.keys(env).length ? { env } : {}),
      });
      setOut(String(r.result ?? ""));
    } catch (e) { setOut(`error: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };
  const save = async (): Promise<void> => {
    if (!out) return;
    if (!window.confirm(`Save to ${savePath}?\nApproval card will surface in the right sidebar.`)) return;
    setSaving(true);
    try {
      await api.invokeTool("programmer", "write_file", { path: savePath, content: out });
      alert("Saved.");
    } catch (e) { alert(`save failed: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-baseline gap-2">
        <FileCode size={14} className="text-accent-blue self-center" />
        <h3 className="font-display text-sm font-semibold text-text-primary">docker-compose service</h3>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="name" value={name} onChange={setName} />
        <Field label="image" value={image} onChange={setImage} />
        <Field label="port" value={String(port)} onChange={(v) => setPort(Number.parseInt(v || "0", 10))} />
      </div>
      <TextArea
        label="env (KEY=VALUE per line)"
        value={envText}
        onChange={setEnvText}
        placeholder={"FOO=bar\nDEBUG=1"}
      />
      <div className="flex gap-2">
        <Button variant="primary" icon={<Sparkles size={12} />} loading={busy} onClick={generate}>
          Generate
        </Button>
        {out && (
          <>
            <Field label="" value={savePath} onChange={setSavePath} placeholder="/tmp/docker-compose.yml" className="flex-1" />
            <Button variant="danger" icon={<Save size={12} />} loading={saving} onClick={save}>
              Save (gated)
            </Button>
          </>
        )}
      </div>
      {out !== null && <CodeBlock text={out} language="yaml" />}
    </Card>
  );
}

// ---------- Helm ----------

function HelmGenerator(): JSX.Element {
  const [service_name, setServiceName] = useState("olympus-app");
  const [image, setImage] = useState("olympus/dashboard");
  const [port, setPort] = useState(8765);
  const [replicas, setReplicas] = useState(1);
  const [out, setOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savePath, setSavePath] = useState("/tmp/values.yaml");
  const [saving, setSaving] = useState(false);

  const generate = async (): Promise<void> => {
    setBusy(true);
    try {
      const r = await api.invokeTool("programmer", "generate_helm_values", {
        service_name, image, port, replicas,
      });
      setOut(String(r.result ?? ""));
    } catch (e) { setOut(`error: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };
  const save = async (): Promise<void> => {
    if (!out) return;
    if (!window.confirm(`Save to ${savePath}?\nApproval card will surface in the right sidebar.`)) return;
    setSaving(true);
    try {
      await api.invokeTool("programmer", "write_file", { path: savePath, content: out });
      alert("Saved.");
    } catch (e) { alert(`save failed: ${(e as Error).message}`); }
    finally { setSaving(false); }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-baseline gap-2">
        <Anchor size={14} className="text-accent-green self-center" />
        <h3 className="font-display text-sm font-semibold text-text-primary">Helm values.yaml</h3>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <Field label="service_name" value={service_name} onChange={setServiceName} />
        <Field label="image" value={image} onChange={setImage} />
        <Field label="port" value={String(port)} onChange={(v) => setPort(Number.parseInt(v || "0", 10))} />
        <Field label="replicas" value={String(replicas)} onChange={(v) => setReplicas(Number.parseInt(v || "0", 10))} />
      </div>
      <div className="flex gap-2">
        <Button variant="primary" icon={<Sparkles size={12} />} loading={busy} onClick={generate}>
          Generate
        </Button>
        {out && (
          <>
            <Field label="" value={savePath} onChange={setSavePath} placeholder="/tmp/values.yaml" className="flex-1" />
            <Button variant="danger" icon={<Save size={12} />} loading={saving} onClick={save}>
              Save (gated)
            </Button>
          </>
        )}
      </div>
      {out !== null && <CodeBlock text={out} language="yaml" />}
    </Card>
  );
}

// ---------- form primitives ----------

function Field({
  label, value, onChange, placeholder, className,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}): JSX.Element {
  return (
    <label className={`block ${className ?? ""}`}>
      {label && (
        <div className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
          {label}
        </div>
      )}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-dark-primary border border-border-subtle rounded px-2 py-1.5 text-sm font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
      />
    </label>
  );
}

function TextArea({
  label, value, onChange, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}): JSX.Element {
  return (
    <label className="block">
      <div className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
        {label}
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={3}
        className="w-full bg-dark-primary border border-border-subtle rounded px-2 py-1.5 text-sm font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60 min-h-[80px] resize-y"
      />
    </label>
  );
}

function Select({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}): JSX.Element {
  return (
    <label className="block">
      <div className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
        {label}
      </div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-dark-primary border border-border-subtle rounded px-2 py-1.5 text-sm font-mono text-text-primary focus:outline-none focus:border-accent-blue/60"
      >
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}
