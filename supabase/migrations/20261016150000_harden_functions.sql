-- Trigger functions are only meant to run from their triggers; nobody should call them directly.
-- (Trigger execution does not check EXECUTE privilege, so the triggers keep working.)
revoke execute on function public.handle_new_user() from public, anon, authenticated;
revoke execute on function public.touch_updated_at() from public, anon, authenticated;
