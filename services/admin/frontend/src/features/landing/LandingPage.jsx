import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Bot, LayoutGrid, Play, Settings2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

function ConceptCard({ icon: Icon, title, description }) {
  return (
    <Card className="border-stone-200/80 bg-white/80 shadow-sm backdrop-blur dark:border-stone-800 dark:bg-stone-950/70">
      <CardHeader className="space-y-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-stone-900 text-stone-50 dark:bg-stone-100 dark:text-stone-950">
          <Icon className="h-5 w-5" />
        </div>
        <div className="space-y-1">
          <CardTitle className="text-xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
      </CardHeader>
    </Card>
  );
}

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.14),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(14,165,233,0.12),_transparent_32%),linear-gradient(180deg,_#f6f3ee_0%,_#fbfaf8_46%,_#f1ede7_100%)] text-stone-950 dark:bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.12),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(14,165,233,0.12),_transparent_34%),linear-gradient(180deg,_#111111_0%,_#151515_48%,_#0f0f0f_100%)] dark:text-stone-50">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-6 py-10 sm:px-8 lg:px-10">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-medium uppercase tracking-[0.3em] text-stone-500 dark:text-stone-400">
              MASE
            </div>
            <div className="mt-1 text-sm text-stone-600 dark:text-stone-300">
              Multi-Agent Simulation Environment
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline">
              <Link to="/environments">Environments</Link>
            </Button>
            <Button asChild>
              <Link to="/runs">Open platform</Link>
            </Button>
          </div>
        </div>

        <section className="grid flex-1 gap-10 py-12 lg:grid-cols-[1.15fr_0.85fr] lg:items-center">
          <div className="space-y-6">
            <div className="space-y-4">
              <h1 className="max-w-4xl text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">
                Run controlled multi-agent experiments in authored environments.
              </h1>
              <p className="max-w-2xl text-lg leading-8 text-stone-700 dark:text-stone-300">
                MASE is a code-first runtime for launching agent populations inside experiment-specific
                environments, then inspecting traces, telemetry, and lifecycle behavior from one operator surface.
              </p>
            </div>

            <div className="rounded-3xl border border-stone-200/80 bg-white/75 p-6 shadow-sm backdrop-blur dark:border-stone-800 dark:bg-stone-950/60">
              <div className="text-sm font-medium uppercase tracking-[0.18em] text-stone-500 dark:text-stone-400">
                How it works
              </div>
              <div className="mt-4 grid gap-3 text-sm text-stone-700 dark:text-stone-300">
                <div><span className="font-medium text-stone-950 dark:text-stone-50">Runtime</span> defines the execution engine and baseline tools.</div>
                <div><span className="font-medium text-stone-950 dark:text-stone-50">Environment</span> is the experiment package: world APIs, populations, skills, prompts, and hooks.</div>
                <div><span className="font-medium text-stone-950 dark:text-stone-50">Run</span> is one concrete execution with inspectable traces and controls.</div>
              </div>
              <div className="mt-5 text-sm text-stone-600 dark:text-stone-400">
                In v1, creating a new experiment means creating a new environment package.
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <Button asChild size="lg">
                <Link to="/runs">
                  Run registry
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link to="/environments">Browse environments</Link>
              </Button>
            </div>
          </div>

          <div className="grid gap-4">
            <ConceptCard
              icon={Play}
              title="Run inspection"
              description="Open active or historical runs, inspect traces, and pause, resume, stop, or delete from the same registry."
            />
            <ConceptCard
              icon={LayoutGrid}
              title="Environment packages"
              description="Keep world logic, populations, prompts, skills, and timed hooks together instead of scattering them across platform objects."
            />
            <ConceptCard
              icon={Bot}
              title="Runtime engines"
              description="Support reusable execution systems without turning experiment-local agent roles into global templates."
            />
            <ConceptCard
              icon={Settings2}
              title="Operator workflow"
              description="Use MASE as a lean operator console: launch runs, inspect behavior, and iterate environments."
            />
          </div>
        </section>
      </div>
    </main>
  );
}
