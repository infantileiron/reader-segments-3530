-- 002_assignment.sql — 批量归群任务、源数据版本号与触达归群引用
-- 本项目无在线迁移执行器：新部署由 ORM create_all 自动建表；已有数据库请手动执行本文件。

CREATE TABLE public.meta (
    key character varying(40) NOT NULL,
    value integer
);

CREATE TABLE public.assignments (
    id integer NOT NULL,
    segment_id integer,
    state character varying(20),
    source_revision integer,
    candidates integer,
    result json,
    error text
);

CREATE SEQUENCE public.assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.assignments_id_seq OWNED BY public.assignments.id;

ALTER TABLE ONLY public.assignments ALTER COLUMN id SET DEFAULT nextval('public.assignments_id_seq'::regclass);

ALTER TABLE ONLY public.meta
    ADD CONSTRAINT meta_pkey PRIMARY KEY (key);

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.assignments
    ADD CONSTRAINT assignments_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);

ALTER TABLE ONLY public.outreaches ADD COLUMN assignment_id integer;
ALTER TABLE ONLY public.outreaches ADD COLUMN assignment_revision integer;
ALTER TABLE ONLY public.outreaches ADD COLUMN assigned_members json;

ALTER TABLE ONLY public.outreaches
    ADD CONSTRAINT outreaches_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.assignments(id);
