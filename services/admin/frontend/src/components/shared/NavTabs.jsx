import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Tabs, TabsList, TabsTrigger } from '../ui/tabs';

export function NavTabs({ items = [] }) {
  const location = useLocation();
  const navigate = useNavigate();

  const activeValue = items.find((item) => {
    if (location.pathname === item.to) return true;
    if (location.pathname.startsWith(`${item.to}/`)) return true;
    if (item.aliases?.some((alias) => location.pathname === alias)) return true;
    if (item.aliases?.some((alias) => location.pathname.startsWith(`${alias}/`))) return true;
    return false;
  })?.to || items[0]?.to || '';

  return (
    <Tabs value={activeValue} onValueChange={(value) => navigate(value)}>
      <TabsList>
        {items.map((item) => (
          <TabsTrigger key={item.to} value={item.to}>
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
