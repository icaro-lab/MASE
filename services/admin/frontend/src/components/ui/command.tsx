import * as React from "react"
import {
  Command as CommandRoot,
  CommandDialog as PrimitiveDialog,
  CommandEmpty as PrimitiveEmpty,
  CommandGroup as PrimitiveGroup,
  CommandInput as PrimitiveInput,
  CommandItem as PrimitiveItem,
  CommandList as PrimitiveList,
  CommandSeparator as PrimitiveSeparator,
} from "cmdk"
import { Search, Terminal } from "lucide-react"

import { cn } from "@/lib/utils"

const Command = React.forwardRef<
  React.ElementRef<typeof CommandRoot>,
  React.ComponentPropsWithoutRef<typeof CommandRoot>
>(({ className, ...props }, ref) => (
  <CommandRoot
    ref={ref}
    className={cn(
      "flex h-full w-full flex-col rounded-lg border border-border bg-popover text-popover-foreground shadow-sm",
      className
    )}
    {...props}
  />
))
Command.displayName = "Command"

const CommandDialog = React.forwardRef<
  React.ElementRef<typeof PrimitiveDialog>,
  React.ComponentPropsWithoutRef<typeof PrimitiveDialog>
>(({ className, ...props }, ref) => (
  <PrimitiveDialog
    ref={ref}
    className={cn("bg-transparent p-0", className)}
    overlayClassName="bg-black/50 fixed inset-0 z-[90] backdrop-blur-[1px]"
    contentClassName="fixed left-1/2 top-[12vh] z-[91] w-[min(820px,calc(100vw-24px))] -translate-x-1/2"
    {...props}
  />
))
CommandDialog.displayName = "CommandDialog"

const CommandInput = React.forwardRef<
  React.ElementRef<typeof PrimitiveInput>,
  React.ComponentPropsWithoutRef<typeof PrimitiveInput>
>(({ className, ...props }, ref) => (
  <div className="flex items-center border-b border-border/70 px-3 py-2">
    <Search className="size-4 shrink-0 text-muted-foreground" />
    <PrimitiveInput
      ref={ref}
      className={cn(
        "w-full bg-transparent py-2 pl-2 text-sm outline-none placeholder:text-muted-foreground",
        className
      )}
      {...props}
    />
  </div>
))
CommandInput.displayName = "CommandInput"

const CommandList = React.forwardRef<
  React.ElementRef<typeof PrimitiveList>,
  React.ComponentPropsWithoutRef<typeof PrimitiveList>
>(({ className, ...props }, ref) => (
  <PrimitiveList
    ref={ref}
    className={cn("max-h-[58vh] overflow-y-auto p-2", className)}
    {...props}
  />
))
CommandList.displayName = "CommandList"

const CommandEmpty = React.forwardRef<
  React.ElementRef<typeof PrimitiveEmpty>,
  React.ComponentPropsWithoutRef<typeof PrimitiveEmpty>
>(({ className, ...props }, ref) => (
  <PrimitiveEmpty
    ref={ref}
    className={cn("px-2 py-3 text-sm text-muted-foreground", className)}
    {...props}
  />
))
CommandEmpty.displayName = "CommandEmpty"

const CommandGroup = React.forwardRef<
  React.ElementRef<typeof PrimitiveGroup>,
  React.ComponentPropsWithoutRef<typeof PrimitiveGroup>
>(({ className, ...props }, ref) => (
  <PrimitiveGroup
    ref={ref}
    className={cn("overflow-hidden rounded-md border border-border/70 bg-background/60 p-2", className)}
    {...props}
  />
))
CommandGroup.displayName = "CommandGroup"

const CommandItem = React.forwardRef<
  React.ElementRef<typeof PrimitiveItem>,
  React.ComponentPropsWithoutRef<typeof PrimitiveItem>
>(({ className, ...props }, ref) => (
  <PrimitiveItem
    ref={ref}
    className={cn(
      "mb-1.5 flex cursor-pointer items-center justify-between gap-3 rounded-md border border-border/70 bg-background/60 px-3 py-2 text-sm last:mb-0 data-[selected=true]:border-primary/40 data-[selected=true]:bg-primary/10",
      className
    )}
    {...props}
  />
))
CommandItem.displayName = "CommandItem"

const CommandShortcut = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span
      ref={ref}
      className={cn("ml-auto text-xs tracking-widest text-muted-foreground", className)}
      {...props}
    />
  )
)
CommandShortcut.displayName = "CommandShortcut"

const CommandSeparator = React.forwardRef<
  React.ElementRef<typeof PrimitiveSeparator>,
  React.ComponentPropsWithoutRef<typeof PrimitiveSeparator>
>(({ className, ...props }, ref) => (
  <PrimitiveSeparator
    ref={ref}
    className={cn("-mx-1 h-px bg-border", className)}
    {...props}
  />
))
CommandSeparator.displayName = "CommandSeparator"

const CommandSubheading = React.forwardRef<
  HTMLSpanElement,
  React.HTMLAttributes<HTMLSpanElement>
>(({ className, ...props }, ref) => (
  <span
    ref={ref}
    className={cn("flex items-center gap-2 px-2 py-1 text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground", className)}
    {...props}
  >
    <Terminal className="size-3.5" aria-hidden />
  </span>
))
CommandSubheading.displayName = "CommandSubheading"

export {
  Command,
  CommandDialog,
  CommandInput,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
  CommandSubheading,
}
