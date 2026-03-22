import * as React from "react"
import { Slot } from "@radix-ui/react-slot"

import { cn } from "@/lib/utils"

const SIDEBAR_COOKIE_NAME = "mase-admin-sidebar-state"
const SIDEBAR_WIDTH = "16rem"
const SIDEBAR_WIDTH_ICON = "3rem"
const MOBILE_BREAKPOINT = 768

type SidebarContextValue = {
  open: boolean
  onOpenChange: (value: boolean) => void
  toggleSidebar: () => void
  isMobile: boolean
}

const SidebarContext = React.createContext<SidebarContextValue | undefined>(undefined)

function useSidebar() {
  const context = React.useContext(SidebarContext)
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.")
  }
  return context
}

function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState(() => {
    if (typeof window === "undefined") return false
    return window.innerWidth < MOBILE_BREAKPOINT
  })

  React.useEffect(() => {
    if (typeof window === "undefined") return
    const media = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => setIsMobile(media.matches)
    onChange()
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [])

  return isMobile
}

function SidebarProvider({
  children,
  defaultOpen = true,
  open,
  onOpenChange,
  className,
}: {
  children: React.ReactNode
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (value: boolean) => void
  className?: string
}) {
  const [internalOpen, setInternalOpen] = React.useState(defaultOpen)
  const controlled = typeof open === "boolean"
  const resolvedOpen = controlled ? open : internalOpen
  const isMobile = useIsMobile()

  const setOpen = React.useCallback(
    (value: boolean) => {
      if (!controlled) {
        setInternalOpen(value)
      }
      onOpenChange?.(value)
    },
    [controlled, onOpenChange]
  )

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem(SIDEBAR_COOKIE_NAME)
    if (saved === null) return;
    const parsed = saved === "open"
    if (!controlled) {
      setInternalOpen(parsed)
    }
  }, [controlled])

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(SIDEBAR_COOKIE_NAME, resolvedOpen ? "open" : "closed")
  }, [resolvedOpen])

  const contextValue = React.useMemo(
    () => ({
      open: resolvedOpen,
      onOpenChange: setOpen,
      toggleSidebar: () => setOpen(!resolvedOpen),
      isMobile,
    }),
    [isMobile, resolvedOpen, setOpen]
  )

  return (
    <SidebarContext.Provider value={contextValue}>
      <div className={className}>{children}</div>
    </SidebarContext.Provider>
  )
}

const Sidebar = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { collapsed?: boolean }
>(({ className, collapsed, children, ...props }, ref) => {
  const { open, isMobile, toggleSidebar } = useSidebar()
  const isOpen = typeof collapsed === "boolean" ? !collapsed : open

  if (isMobile) {
    return (
      <>
        {isOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50"
            onClick={toggleSidebar}
          />
        )}
        <aside
          ref={ref}
          data-state={isOpen ? "expanded" : "collapsed"}
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex flex-col bg-sidebar text-sidebar-foreground border-r border-sidebar-border transition-transform duration-200",
            isOpen ? "translate-x-0" : "-translate-x-full",
            className
          )}
          style={{ width: SIDEBAR_WIDTH }}
          {...props}
        >
          <div className="flex h-full w-full flex-col overflow-hidden">{children}</div>
        </aside>
      </>
    )
  }

  return (
    <aside
      ref={ref}
      data-state={isOpen ? "expanded" : "collapsed"}
      data-collapsible={isOpen ? "" : "icon"}
      className={cn(
        "group/sidebar flex h-full shrink-0 flex-col bg-sidebar text-sidebar-foreground border-r border-sidebar-border transition-[width] duration-200 overflow-hidden",
        className
      )}
      style={{ width: isOpen ? SIDEBAR_WIDTH : SIDEBAR_WIDTH_ICON }}
      {...props}
    >
      <div className="flex h-full w-full flex-col overflow-hidden">{children}</div>
    </aside>
  )
})
Sidebar.displayName = "Sidebar"

const SidebarHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex shrink-0 items-center border-b border-sidebar-border px-3 py-3 group-[[data-collapsible=icon]]/sidebar:px-1.5", className)}
    {...props}
  />
))
SidebarHeader.displayName = "SidebarHeader"

const SidebarContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-2 py-2", className)} {...props} />
))
SidebarContent.displayName = "SidebarContent"

const SidebarInset = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("min-w-0 flex-1", className)}
    {...props}
  />
))
SidebarInset.displayName = "SidebarInset"

const SidebarFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("shrink-0 border-t border-sidebar-border p-2 group-[[data-collapsible=icon]]/sidebar:px-1.5", className)}
    {...props}
  />
))
SidebarFooter.displayName = "SidebarFooter"

const SidebarGroup = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("space-y-1 px-1", className)}
    {...props}
  />
))
SidebarGroup.displayName = "SidebarGroup"

const SidebarGroupLabel = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h4
    ref={ref}
    className={cn(
      "truncate px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-sidebar-foreground/50",
      "group-[[data-collapsible=icon]]/sidebar:hidden",
      className
    )}
    {...props}
  />
))
SidebarGroupLabel.displayName = "SidebarGroupLabel"

const SidebarGroupContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("space-y-0.5", className)} {...props} />
))
SidebarGroupContent.displayName = "SidebarGroupContent"

const SidebarMenu = React.forwardRef<
  HTMLUListElement,
  React.HTMLAttributes<HTMLUListElement>
>(({ className, ...props }, ref) => (
  <ul ref={ref} className={cn("space-y-0.5", className)} {...props} />
))
SidebarMenu.displayName = "SidebarMenu"

const SidebarMenuItem = React.forwardRef<
  HTMLLIElement,
  React.HTMLAttributes<HTMLLIElement>
>(({ className, ...props }, ref) => (
  <li ref={ref} className={cn("group/menu-item relative", className)} {...props} />
))
SidebarMenuItem.displayName = "SidebarMenuItem"

const SidebarMenuButton = React.forwardRef<
  HTMLElement,
  React.ComponentProps<"button"> & {
    asChild?: boolean
    isActive?: boolean
    size?: "default" | "sm" | "lg"
    tooltip?: string
  }
>(({ className, asChild, isActive, size = "default", tooltip, ...props }, ref) => {
  const Comp = asChild ? Slot : "button"
  const sizeClass =
    size === "sm"
      ? "h-7 px-2 text-xs"
      : size === "lg"
        ? "h-10 px-3 text-sm"
        : "h-8 px-2 text-sm"
  return (
    <Comp
      ref={ref}
      title={tooltip}
      data-active={isActive || undefined}
      className={cn(
        "flex w-full items-center gap-2 overflow-hidden rounded-md font-medium text-left transition-colors outline-none",
        "[&>svg]:size-4 [&>svg]:shrink-0",
        "group-[[data-collapsible=icon]]/sidebar:justify-center group-[[data-collapsible=icon]]/sidebar:px-0 group-[[data-collapsible=icon]]/sidebar:[&>span]:hidden group-[[data-collapsible=icon]]/sidebar:[&>div]:hidden",
        sizeClass,
        isActive
          ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold"
          : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        className
      )}
      {...props}
    />
  )
})
SidebarMenuButton.displayName = "SidebarMenuButton"

const SidebarMenuAction = React.forwardRef<
  HTMLButtonElement,
  React.ComponentProps<"button"> & { showOnHover?: boolean }
>(({ className, showOnHover, ...props }, ref) => (
  <button
    ref={ref}
    type="button"
    className={cn(
      "absolute right-1 top-1.5 flex h-6 w-6 items-center justify-center rounded-md text-sidebar-foreground/50 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground [&>svg]:size-3.5",
      "group-[[data-collapsible=icon]]/sidebar:hidden",
      showOnHover && "opacity-0 group-hover/menu-item:opacity-100 group-focus-within/menu-item:opacity-100",
      className
    )}
    {...props}
  />
))
SidebarMenuAction.displayName = "SidebarMenuAction"

const SidebarMenuSub = React.forwardRef<
  HTMLUListElement,
  React.HTMLAttributes<HTMLUListElement>
>(({ className, ...props }, ref) => (
  <ul
    ref={ref}
    className={cn("ml-3.5 space-y-0.5 border-l border-sidebar-border pl-2.5 pt-0.5 group-[[data-collapsible=icon]]/sidebar:hidden", className)}
    {...props}
  />
))
SidebarMenuSub.displayName = "SidebarMenuSub"

const SidebarMenuSubItem = React.forwardRef<
  HTMLLIElement,
  React.HTMLAttributes<HTMLLIElement>
>(({ className, ...props }, ref) => (
  <li ref={ref} className={cn(className)} {...props} />
))
SidebarMenuSubItem.displayName = "SidebarMenuSubItem"

const SidebarMenuSubButton = React.forwardRef<
  HTMLElement,
  React.ComponentProps<"button"> & { asChild?: boolean; isActive?: boolean }
>(({ className, asChild, isActive, ...props }, ref) => {
  const Comp = asChild ? Slot : "button"
  return (
    <Comp
      ref={ref}
      data-active={isActive || undefined}
      className={cn(
        "flex w-full items-center rounded-md px-2 py-1 text-xs transition-colors",
        isActive
          ? "text-sidebar-accent-foreground font-medium"
          : "text-sidebar-foreground/60 hover:text-sidebar-accent-foreground",
        className
      )}
      {...props}
    />
  )
})
SidebarMenuSubButton.displayName = "SidebarMenuSubButton"

const SidebarInput = React.forwardRef<
  HTMLInputElement,
  React.ComponentProps<"input">
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "flex h-8 w-full rounded-md border border-sidebar-border bg-sidebar px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-sidebar-foreground/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sidebar-ring",
      className
    )}
    {...props}
  />
))
SidebarInput.displayName = "SidebarInput"

function SidebarTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<"button"> & { children?: React.ReactNode }) {
  const { open, toggleSidebar } = useSidebar()
  return (
    <button
      type="button"
      onClick={toggleSidebar}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground",
        className
      )}
      aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
      {...props}
    >
      {children}
    </button>
  )
}

export {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarInput,
  SidebarTrigger,
  useSidebar,
}
