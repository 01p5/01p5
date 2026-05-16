import { useEffect, useState } from "react";
import { Layers, Play, FileSearch, CheckCircle2, ListTree, AlertTriangle, Wand2 } from "lucide-react";
import { api } from "../api";
import { Card, SectionHeader } from "../components/Card";
import { Button } from "../components/Button";
import { Modal } from "../components/Modal";
import { CodeBlock } from "../components/CodeBlock";
import { Badge } from "../components/Badge";

type ToolAction = "tf_init" | "tf_validate" | "tf_plan" | "tf_show" | "tf_output";

interface OutputModal {
  title: string;
  text: string;
  primaryAction?: { label: string; onClick: () => void; loading?: boolean };
  destructive?: boolean;
}

/**
 * Terraform — stack cards, plan→apply modal flow. Each card runs the
 * full read-only loop (init/validate/plan/show/output) on demand, and
 * the destructive apply/destroy buttons go through the approval queue.
 * Click "Plan" → modal shows the plan output with an "Apply this plan"
 * button at the bottom (fires tf_apply → approval card surfaces in the
 * right sidebar).
 */
export function TerraformPage(): JSX.Element {
  const [stacks, setStacks] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [modal, setModal] = useState<OutputModal | null>(null);

  useEffect(() => {
    api.terraformStacks().then(setStacks).catch((e) => setError((e as Error).message));
  }, []);

  /** Resolve a stack name (e.g. "terraform/aws") to a working_dir that
   * exists inside the container ("/opt/olympus/infra/terraform/aws"). */
  const stackToWorkingDir = (stack: string): string => `/opt/olympus/infra/${stack}`;

  const runReadOnly = async (
    stack: string,
    action: ToolAction,
    title: string,
    args: Record<string, unknown> = {},
  ): Promise<void> => {
    setBusyKey(`${stack}:${action}`);
    try {
      const r = await api.invokeTool("terraform", action, {
        working_dir: stackToWorkingDir(stack), ...args,
      });
      const text = String(r.result ?? "(empty)");
      const isPlan = action === "tf_plan";
      setModal({
        title: `${title} — ${stack}`,
        text,
        primaryAction: isPlan
          ? {
              label: "Apply this plan",
              onClick: () => runApply(stack),
            }
          : undefined,
      });
    } catch (e) {
      setModal({ title: `${action} — ${stack} (error)`, text: (e as Error).message });
    } finally {
      setBusyKey(null);
    }
  };

  const runApply = async (stack: string): Promise<void> => {
    if (
      !window.confirm(
        `Apply terraform changes to ${stack}?\n\n` +
        `This is a destructive operation. The Olympus runtime will queue ` +
        `an approval card in the right sidebar before any real change is ` +
        `made — you'll need to approve it there.`,
      )
    ) return;
    setBusyKey(`${stack}:tf_apply`);
    try {
      const r = await api.invokeTool("terraform", "tf_apply", {
        working_dir: stackToWorkingDir(stack),
      });
      setModal({
        title: `Apply result — ${stack}`,
        text: String(r.result ?? "(empty)"),
      });
    } catch (e) {
      setModal({
        title: `Apply failed — ${stack}`,
        text: (e as Error).message,
        destructive: true,
      });
    } finally {
      setBusyKey(null);
    }
  };

  const runDestroy = async (stack: string): Promise<void> => {
    if (
      !window.confirm(
        `DESTROY all resources managed by ${stack}?\n\n` +
        `This will permanently delete every resource in this stack. ` +
        `An approval card will still surface in the right sidebar.`,
      )
    ) return;
    setBusyKey(`${stack}:tf_destroy`);
    try {
      const r = await api.invokeTool("terraform", "tf_destroy", {
        working_dir: stackToWorkingDir(stack),
      });
      setModal({ title: `Destroy result — ${stack}`, text: String(r.result ?? "(empty)") });
    } catch (e) {
      setModal({
        title: `Destroy failed — ${stack}`,
        text: (e as Error).message,
        destructive: true,
      });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className="flex flex-col min-h-0 h-full">
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-3">
        <Layers size={14} className="text-accent-blue self-center" />
        <h1 className="font-display text-base font-semibold text-text-primary">Terraform</h1>
        <span className="text-[11px] font-mono text-text-muted">
          {stacks?.length ?? "…"} stack{stacks?.length === 1 ? "" : "s"} discovered
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-5">
        {error && (
          <Card className="p-4 text-accent-red font-mono text-sm">
            <AlertTriangle size={14} className="inline mr-2" />
            {error}
          </Card>
        )}
        {stacks === null && (
          <div className="text-text-muted italic text-sm">loading stacks…</div>
        )}
        {stacks?.length === 0 && (
          <Card className="p-6 text-text-muted">
            No terraform stacks found under <code>/opt/olympus/infra/terraform</code>.
            Ensure the chart's image was built with <code>infra/terraform/</code> copied in.
          </Card>
        )}
        {stacks && stacks.length > 0 && (
          <>
            <SectionHeader
              title="Stacks"
              hint="from infra/terraform/ in the container"
            />
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {stacks.map((s) => (
                <StackCard
                  key={s}
                  stack={s}
                  busyKey={busyKey}
                  onReadOnly={runReadOnly}
                  onApply={runApply}
                  onDestroy={runDestroy}
                />
              ))}
            </div>
          </>
        )}
      </div>

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.title ?? ""}
        wide
        headerAction={
          modal?.primaryAction && (
            <Button
              variant="primary"
              size="sm"
              icon={<CheckCircle2 size={12} />}
              onClick={() => {
                modal.primaryAction!.onClick();
                setModal(null);
              }}
            >
              {modal.primaryAction.label}
            </Button>
          )
        }
      >
        {modal && <CodeBlock text={modal.text} language="hcl" maxHeight="65vh" />}
      </Modal>
    </section>
  );
}

function StackCard({
  stack, busyKey, onReadOnly, onApply, onDestroy,
}: {
  stack: string;
  busyKey: string | null;
  onReadOnly: (stack: string, action: ToolAction, title: string, args?: Record<string, unknown>) => Promise<void>;
  onApply: (stack: string) => Promise<void>;
  onDestroy: (stack: string) => Promise<void>;
}): JSX.Element {
  const isBusy = (k: string): boolean => busyKey === `${stack}:${k}`;
  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-mono text-sm text-text-primary truncate">{stack}</h3>
        <Badge tone="blue">terraform</Badge>
      </div>
      <div className="text-[11px] text-text-muted font-mono break-all">
        /opt/olympus/infra/{stack}
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <Button
          size="sm"
          icon={<Wand2 size={12} />}
          loading={isBusy("tf_init")}
          onClick={() => onReadOnly(stack, "tf_init", "Init")}
        >
          init
        </Button>
        <Button
          size="sm"
          icon={<CheckCircle2 size={12} />}
          loading={isBusy("tf_validate")}
          onClick={() => onReadOnly(stack, "tf_validate", "Validate")}
        >
          validate
        </Button>
        <Button
          size="sm"
          icon={<FileSearch size={12} />}
          loading={isBusy("tf_plan")}
          onClick={() => onReadOnly(stack, "tf_plan", "Plan")}
        >
          plan
        </Button>
        <Button
          size="sm"
          icon={<ListTree size={12} />}
          loading={isBusy("tf_show")}
          onClick={() => onReadOnly(stack, "tf_show", "Show")}
        >
          show
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-1.5 pt-1 border-t border-border-subtle">
        <Button
          size="sm"
          variant="primary"
          icon={<Play size={12} />}
          loading={isBusy("tf_apply")}
          onClick={() => onApply(stack)}
        >
          apply
        </Button>
        <Button
          size="sm"
          variant="danger"
          icon={<AlertTriangle size={12} />}
          loading={isBusy("tf_destroy")}
          onClick={() => onDestroy(stack)}
        >
          destroy
        </Button>
      </div>
    </Card>
  );
}
