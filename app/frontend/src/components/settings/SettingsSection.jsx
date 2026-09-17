export default function SettingsSection({ section, children, t }) {
  return (
    <section className="rounded-lg border border-gray-200 p-4 md:col-span-2 dark:border-slate-600">
      <h3 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-100">{t(section.label_key)}</h3>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{children}</div>
    </section>
  );
}
