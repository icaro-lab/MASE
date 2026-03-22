import React from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';

const EMPTY_VALUE = '__empty__';

function encodeSelectValue(value) {
  if (value === null || value === undefined) return undefined;
  const stringified = String(value);
  return stringified === '' ? EMPTY_VALUE : stringified;
}

function decodeSelectValue(value) {
  return value === EMPTY_VALUE ? '' : value;
}

export function SelectField({
  id,
  ariaLabel,
  value,
  options = [],
  onChange,
  placeholder,
  className,
  triggerClassName,
}) {
  const normalizedValue = encodeSelectValue(value);
  const selected = options.find((option) => encodeSelectValue(option.value) === normalizedValue);

  return (
    <div className={className}>
      <Select value={normalizedValue} onValueChange={(nextValue) => onChange?.(decodeSelectValue(nextValue))}>
        <SelectTrigger id={id} aria-label={ariaLabel} className={triggerClassName}>
          <SelectValue placeholder={placeholder || selected?.label || 'Select option'} />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={encodeSelectValue(option.value)} value={encodeSelectValue(option.value)}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
