-- این اسکریپت را یک‌بار در Supabase → SQL Editor اجرا کنید تا جدول‌های
-- موردنیاز ربات ساخته شوند. بعد از اجرا، ربات خودش داده‌ها را می‌خواند/می‌نویسد.
--
-- نکته: چون ربات با کلید service_role (Secret key) وصل می‌شود، نیازی به
-- تنظیم Row Level Security (RLS) نیست — این کلید همیشه RLS را دور می‌زند.
-- پس این جدول‌ها را RLS-enabled نکنید (پیش‌فرض هم غیرفعال است).

create table if not exists admins (
    user_id     bigint primary key,
    is_owner    boolean default false,
    permissions text    default '',
    added_by    bigint
);

create table if not exists rules (
    id            bigint generated always as identity primary key,
    source_id     bigint not null,
    source_title  text,
    source_kind   text not null,
    target_id     bigint not null,
    target_title  text,
    target_kind   text not null,
    direction     text not null,
    active        boolean default false,
    created_by    bigint,
    unique (source_id, target_id)
);
