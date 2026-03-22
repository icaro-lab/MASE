"use client"

import * as React from "react"
import {
  LayoutGrid,
  Play,
  Settings2,
} from "lucide-react"
import { Link } from "react-router-dom"

import { NavMain } from "@/registry/new-york-v4/blocks/sidebar-16/components/nav-main"
import { NavSecondary } from "@/registry/new-york-v4/blocks/sidebar-16/components/nav-secondary"
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

const data = {
  navMain: [
    {
      title: "Runs",
      url: "/runs",
      icon: Play,
      items: [
        { title: "All Runs", url: "/runs" },
      ],
    },
    {
      title: "Environments",
      url: "/environments",
      icon: LayoutGrid,
      items: [
        { title: "All Environments", url: "/environments" },
      ],
    },
    {
      title: "Config",
      url: "/config",
      icon: Settings2,
    },
  ],
  navSecondary: [] as { title: string; url: string; icon: React.ComponentType }[],
}

interface AppSidebarProps extends React.ComponentProps<typeof Sidebar> {
}

export function AppSidebar({ ...props }: AppSidebarProps) {
  return (
    <Sidebar {...props}>
      <SidebarHeader className="h-14 py-0">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild className="group-[[data-collapsible=icon]]/sidebar:!justify-center group-[[data-collapsible=icon]]/sidebar:!px-0">
              <Link to="/">
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground group-[[data-collapsible=icon]]/sidebar:!flex">
                  <img
                    src="/icaro-logo-wing.svg"
                    className="size-5 object-contain brightness-0 invert dark:invert-0"
                    alt=""
                    aria-hidden
                  />
                </div>
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-semibold">MASE</span>
                  <span className="truncate text-[10px] text-sidebar-foreground/50">
                    Multi-Agent Simulation Environment
                  </span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={data.navMain} />
        {data.navSecondary.length > 0 && (
          <NavSecondary items={data.navSecondary} className="mt-auto" />
        )}
      </SidebarContent>
    </Sidebar>
  )
}
