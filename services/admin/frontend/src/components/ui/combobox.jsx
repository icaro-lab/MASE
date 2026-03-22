import React, { useMemo, useState } from 'react';
import { Check, ChevronsUpDown } from 'lucide-react';
import { cn } from 'lib/utils';
import { Button } from './button';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from './command';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

function stringValueOf(value) {
  if (value === null || value === undefined) return '';
  return String(value);
}

export function Combobox({
  id,
  value = '',
  onValueChange,
  options = [],
  placeholder = 'Select option',
  searchPlaceholder = 'Search option',
  emptyLabel = 'No options found.',
  disabled = false,
  className,
  triggerClassName,
  contentClassName,
  ariaLabel,
}) {
  const [open, setOpen] = useState(false);
  const normalizedValue = stringValueOf(value);
  const normalizedOptions = useMemo(
    () =>
      options.map((option) => ({
        value: stringValueOf(option.value),
        label: option.label,
        description: option.description,
      })),
    [options]
  );
  const selectedOption = normalizedOptions.find((option) => option.value === normalizedValue) || null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          disabled={disabled}
          className={cn('w-full justify-between font-normal', triggerClassName, className)}
        >
          <span className="truncate">
            {selectedOption ? selectedOption.label : placeholder}
          </span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className={cn('w-[--radix-popover-trigger-width] p-0', contentClassName)}>
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{emptyLabel}</CommandEmpty>
            <CommandGroup>
              {normalizedOptions.map((option) => (
                <CommandItem
                  key={option.value}
                  value={`${option.label} ${option.value}`}
                  onSelect={() => {
                    onValueChange?.(option.value);
                    setOpen(false);
                  }}
                >
                  <div className="min-w-0">
                    <span className="truncate">{option.label}</span>
                    {option.description ? (
                      <p className="truncate text-xs text-muted-foreground">{option.description}</p>
                    ) : null}
                  </div>
                  <Check
                    className={cn(
                      'ml-auto h-4 w-4',
                      option.value === normalizedValue ? 'opacity-100' : 'opacity-0'
                    )}
                  />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
