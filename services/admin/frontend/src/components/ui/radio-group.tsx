import * as React from "react"

import { cn } from "@/lib/utils"

type RadioGroupContextValue = {
  name: string
  value: string
  onValueChange?: (nextValue: string) => void
  disabled?: boolean
}

const RadioGroupContext = React.createContext<RadioGroupContextValue | null>(null)

interface RadioGroupProps extends React.HTMLAttributes<HTMLDivElement> {
  name?: string
  value?: string
  defaultValue?: string
  onValueChange?: (nextValue: string) => void
  disabled?: boolean
}

const RadioGroup = React.forwardRef<HTMLDivElement, RadioGroupProps>(
  (
    {
      className,
      name,
      value,
      defaultValue = "",
      onValueChange,
      disabled,
      ...props
    },
    ref
  ) => {
    const generatedName = React.useId()
    const [internalValue, setInternalValue] = React.useState(defaultValue)
    const resolvedValue = value ?? internalValue

    const handleValueChange = React.useCallback(
      (nextValue: string) => {
        if (value === undefined) {
          setInternalValue(nextValue)
        }
        onValueChange?.(nextValue)
      },
      [onValueChange, value]
    )

    const contextValue = React.useMemo(
      () => ({
        name: name || generatedName,
        value: resolvedValue,
        onValueChange: handleValueChange,
        disabled,
      }),
      [disabled, generatedName, handleValueChange, name, resolvedValue]
    )

    return (
      <RadioGroupContext.Provider value={contextValue}>
        <div
          ref={ref}
          role="radiogroup"
          aria-disabled={disabled}
          className={cn("grid gap-2", className)}
          {...props}
        />
      </RadioGroupContext.Provider>
    )
  }
)
RadioGroup.displayName = "RadioGroup"

interface RadioGroupItemProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "name" | "value" | "onChange"> {
  value: string
}

const RadioGroupItem = React.forwardRef<HTMLInputElement, RadioGroupItemProps>(
  ({ className, disabled, value, ...props }, ref) => {
    const context = React.useContext(RadioGroupContext)
    if (!context) {
      throw new Error("RadioGroupItem must be used within RadioGroup")
    }

    const isChecked = context.value === value
    const isDisabled = Boolean(context.disabled || disabled)

    return (
      <input
        {...props}
        ref={ref}
        type="radio"
        name={context.name}
        value={value}
        checked={isChecked}
        disabled={isDisabled}
        onChange={(event) => context.onValueChange?.(event.target.value)}
        className={cn(
          "h-4 w-4 shrink-0 appearance-none rounded-full border border-primary text-primary shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
          "before:block before:h-2 before:w-2 before:translate-x-[3px] before:translate-y-[3px] before:rounded-full before:bg-primary before:opacity-0 before:transition-opacity",
          isChecked ? "before:opacity-100" : "",
          className
        )}
      />
    )
  }
)
RadioGroupItem.displayName = "RadioGroupItem"

export { RadioGroup, RadioGroupItem }
