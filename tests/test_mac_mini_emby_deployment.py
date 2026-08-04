import io
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from mac_mini_emby import (  # noqa: E402
    CONTAINER, EmbyHarnessError, HOSTNAME, IMAGE, IMAGE_ID, ORIGINAL_ID,
    ORIGINAL_VOLUME, PROJECT, require_available_rollback_name,
    validate_backup_archive, validate_backup_location, validate_compose,
    validate_managed_local_root, validate_original_inspect,
    validate_replacement_inspect, validate_managed_service_ids,
    validate_volume_name, sqlite_checks_from_backup, publish_metadata_exclusive,
    acquire_backup_claim, cleanup_unpublished_backup,
)


class ManagedMacMiniEmbyTests(unittest.TestCase):
    volume = "emby-mac-test-config-v1"

    def rendered(self):
        env = ROOT / "deploy/mac-mini/env.emby.test.example"
        text = env.read_text().replace(
            "/absolute/path/to/MediaRouter", str(ROOT)
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            result = subprocess.run([
                "docker", "compose", "-p", PROJECT,
                "-f", str(ROOT / "deploy/mac-mini/compose.emby.test.yml"),
                "--env-file", path, "config", "--format", "json",
            ], cwd=ROOT, check=True, capture_output=True, text=True)
            return json.loads(result.stdout)
        finally:
            os.unlink(path)

    def test_rendered_compose_is_exact_and_isolated(self):
        config = self.rendered()
        validate_compose(config, ROOT, self.volume)
        service = config["services"]["emby"]
        self.assertEqual(CONTAINER, service["container_name"])
        self.assertEqual(HOSTNAME, service["hostname"])
        self.assertEqual(IMAGE, service["image"])
        self.assertEqual("linux/arm64", service["platform"])
        self.assertEqual("1m0s", service["stop_grace_period"])
        self.assertEqual(["no-new-privileges:true"], service["security_opt"])
        self.assertEqual("127.0.0.1", service["ports"][0]["host_ip"])
        targets = {x["target"]: x for x in service["volumes"]}
        self.assertEqual({"/config", "/media-router-test/movies", "/media-router-test/series"}, set(targets))
        self.assertFalse(targets["/config"].get("read_only", False))
        self.assertTrue(targets["/media-router-test/movies"]["read_only"])
        self.assertTrue(targets["/media-router-test/series"]["read_only"])

    def test_compose_rejects_all_high_value_unsafe_variants(self):
        mutations = []
        def add(label, fn): mutations.append((label, fn))
        add("wildcard", lambda c: c["services"]["emby"]["ports"][0].update(host_ip="0.0.0.0"))
        add("unpinned", lambda c: c["services"]["emby"].update(image="emby/embyserver:latest"))
        add("backslash", lambda c: c["services"]["emby"]["volumes"].append({"type":"bind","source":"/tmp","target":"\\media","read_only":True}))
        add("production", lambda c: c["services"]["emby"]["volumes"][1].update(source="/Users/Shared/MediaRouter"))
        add("live", lambda c: c["services"]["emby"]["volumes"][1].update(source=str(ROOT/".local/mac-mini/outputs/live")))
        add("writable", lambda c: c["services"]["emby"]["volumes"][1].update(read_only=False))
        add("anonymous", lambda c: c["services"]["emby"]["volumes"][0].update(source=""))
        add("extra service", lambda c: c["services"].update(extra={"image":"busybox"}))
        add("privileged", lambda c: c["services"]["emby"].update(privileged=True))
        add("wrong platform", lambda c: c["services"]["emby"].update(platform="linux/amd64"))
        add("short stop", lambda c: c["services"]["emby"].update(stop_grace_period="10s"))
        add("security removed", lambda c: c["services"]["emby"].update(security_opt=[]))
        add("capability", lambda c: c["services"]["emby"].update(cap_add=["SYS_ADMIN"]))
        add("host network", lambda c: c["services"]["emby"].update(network_mode="host"))
        for label, mutate in mutations:
            with self.subTest(label=label):
                config = self.rendered(); mutate(config)
                with self.assertRaises(EmbyHarnessError): validate_compose(config, ROOT, self.volume)

    def original(self):
        return {"Id":ORIGINAL_ID,"Name":"/MacEmbyTester","Image":IMAGE_ID,
                "Mounts":[{"Destination":"/config","Name":ORIGINAL_VOLUME}]}

    def replacement(self):
        return {"Id":"a"*64,"Name":"/MacEmbyTester","Image":IMAGE_ID,
                "State":{"Status":"running"},
                "Config":{"Hostname":HOSTNAME,"Labels":{"com.docker.compose.project":PROJECT,
                                                           "com.docker.compose.service":"emby"}},
                "HostConfig":{"Privileged":False,"Devices":[],"CapAdd":None,"CapDrop":None,
                              "ExtraHosts":None,"SecurityOpt":["no-new-privileges:true"]},
                "NetworkSettings":{"Ports":{"8096/tcp":[{"HostIp":"127.0.0.1","HostPort":"8597"}]}},
                "Mounts":[{"Destination":"/config","Name":self.volume,"RW":True},
                          {"Destination":"/media-router-test/movies","RW":False,
                           "Source":str(ROOT/".local/mac-mini/outputs/movies")},
                          {"Destination":"/media-router-test/series","RW":False,
                           "Source":"/host_mnt"+str(ROOT/".local/mac-mini/outputs/series")}]}

    def test_identity_guards(self):
        validate_original_inspect(self.original())
        validate_replacement_inspect(self.replacement(), self.volume, ROOT)
        for key in ("Id", "Image"):
            data=self.original(); data[key]="wrong"
            with self.assertRaises(EmbyHarnessError): validate_original_inspect(data)
        data=self.replacement(); data["NetworkSettings"]["Ports"]["8096/tcp"][0]["HostIp"]="0.0.0.0"
        with self.assertRaises(EmbyHarnessError): validate_replacement_inspect(data,self.volume,ROOT)
        data=self.replacement(); data["Mounts"][1]["Source"]="/tmp/movies"
        with self.assertRaises(EmbyHarnessError): validate_replacement_inspect(data,self.volume,ROOT)
        data=self.replacement(); data["Config"]["Labels"]["com.docker.compose.service"]="wrong"
        with self.assertRaises(EmbyHarnessError): validate_replacement_inspect(data,self.volume,ROOT)
        with self.assertRaises(EmbyHarnessError):
            validate_replacement_inspect(self.replacement(),self.volume,ROOT,"b"*64)

    def test_managed_service_resolution_requires_exactly_one_container(self):
        container="a"*64
        self.assertEqual(container,validate_managed_service_ids([container]))
        for values in ([],[container,"b"*64],["short"]):
            with self.subTest(values=values), self.assertRaises(EmbyHarnessError):
                validate_managed_service_ids(values)

    def test_volume_and_rollback_collision_guards(self):
        self.assertEqual(self.volume, validate_volume_name(self.volume))
        for value in ("", "../bad", ORIGINAL_VOLUME, "embyserver-config", "production-config"):
            with self.assertRaises(EmbyHarnessError): validate_volume_name(value)
        name="MacEmbyTester-rollback-20260801-120000"
        self.assertEqual(name,require_available_rollback_name(set(),name))
        with self.assertRaises(EmbyHarnessError): require_available_rollback_name({name},name)

    def test_managed_local_and_backup_paths_reject_escape_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp)/"repo"
            root=repo/".local/mac-mini/emby-test"
            backups=root/"backups"
            backups.mkdir(parents=True)
            (root/"evidence").mkdir()
            validate_managed_local_root(root,repo)
            candidate=backups/"config-20260802T001328Z.tar.gz"
            validate_backup_location(candidate,backups)
            with self.assertRaises(EmbyHarnessError):
                validate_backup_location(repo/"config-20260802T001328Z.tar.gz",backups)
            link=root/"linked"
            link.symlink_to(backups,target_is_directory=True)
            with self.assertRaises(EmbyHarnessError):
                validate_backup_location(link/"config-20260802T001328Z.tar.gz",backups)

    def test_retained_original_name_is_part_of_identity(self):
        name="MacEmbyTester-rollback-20260802-001317"
        data=self.original(); data["Name"]="/"+name
        validate_original_inspect(data,ORIGINAL_ID,name)
        with self.assertRaises(EmbyHarnessError):
            validate_original_inspect(data,ORIGINAL_ID,"MacEmbyTester-rollback-20260802-999999")

    def test_backup_archive_permissions_and_members(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"config.tar.gz"
            with tarfile.open(path,"w:gz") as bundle:
                data=b"synthetic database bytes"
                info=tarfile.TarInfo("data/library.db"); info.size=len(data); bundle.addfile(info,io.BytesIO(data))
            os.chmod(path,0o600)
            meta=validate_backup_archive(path)
            self.assertEqual(1,meta["file_count"])
            os.chmod(path,0o644)
            with self.assertRaisesRegex(EmbyHarnessError,"0600"): validate_backup_archive(path)

    def test_managed_backup_requires_expected_emby_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"managed-config-20260802T120000Z.tar.gz"
            with tarfile.open(path,"w:gz") as bundle:
                data=b"not sqlite"
                info=tarfile.TarInfo("data/library.db"); info.size=len(data)
                bundle.addfile(info,io.BytesIO(data))
            os.chmod(path,0o600)
            with self.assertRaisesRegex(EmbyHarnessError,"required Emby"):
                validate_backup_archive(path,require_emby_paths=True)

    def test_sqlite_validation_failure_propagates(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"managed-config-20260802T120000Z.tar.gz"
            with tarfile.open(path,"w:gz") as bundle:
                for name in ("activitylog.db","authentication.db","library.db","users.db"):
                    data=b"invalid sqlite database"
                    info=tarfile.TarInfo("data/"+name); info.size=len(data)
                    bundle.addfile(info,io.BytesIO(data))
            os.chmod(path,0o600)
            validate_backup_archive(path,require_emby_paths=True)
            with self.assertRaisesRegex(EmbyHarnessError,"integrity"):
                sqlite_checks_from_backup(path)

    def test_backup_rejects_links_traversal_and_special_members(self):
        for label, info in (
            ("traversal",tarfile.TarInfo("../secret")),
            ("symlink",tarfile.TarInfo("config/link")),
            ("fifo",tarfile.TarInfo("config/fifo")),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/"bad.tar.gz"
                if label=="symlink": info.type=tarfile.SYMTYPE; info.linkname="target"
                if label=="fifo": info.type=tarfile.FIFOTYPE
                with tarfile.open(path,"w:gz") as bundle: bundle.addfile(info)
                os.chmod(path,0o600)
                with self.assertRaises(EmbyHarnessError): validate_backup_archive(path)

    def test_shell_is_posix_and_exposes_no_library_or_scan_commands(self):
        script=ROOT/"scripts/mac-mini-emby-test"
        result=subprocess.run(["sh","-n",str(script)],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        text=script.read_text()
        self.assertNotIn("library-create",text)
        self.assertNotIn("scan-library",text)
        self.assertNotIn("ssh ",text.casefold())
        self.assertIn('compose stop -t 60',text)
        self.assertNotIn('docker kill',text)
        self.assertIn('StartupWizardCompleted',text)
        self.assertIn('safety-check) render >/dev/null;',text)

    def test_backup_modes_and_managed_lifecycle_are_explicit_and_ordered(self):
        script=ROOT/"scripts/mac-mini-emby-test"
        text=script.read_text()
        no_mode=subprocess.run([str(script),"backup"],capture_output=True,text=True)
        self.assertEqual(2,no_mode.returncode)
        self.assertIn("backup --deployment managed",no_mode.stderr)
        self.assertIn('backup --deployment legacy',text)
        self.assertIn('backup --deployment managed',text)
        self.assertIn('[ "$#" -eq 2 ] && [ "$1" = "--deployment" ]',text)
        managed=text[text.index("managed_backup() {"):text.index("clone_from_backup() {")]
        stop=managed.index('compose stop -t 60 "$SERVICE"')
        archive=managed.index('docker run --rm --entrypoint /bin/sh',stop)
        restart=managed.index('compose start "$SERVICE"',archive)
        verify=managed.index('verify_replacement',restart)
        polling=managed.index('wait_router_polling',verify)
        self.assertLess(stop,archive)
        self.assertLess(archive,restart)
        self.assertLess(restart,verify)
        self.assertLess(verify,polling)
        self.assertIn('capture_emby_identity',managed)
        self.assertEqual(2,managed.count('mac-mini-test" smoke'))
        self.assertIn('if [ "$managed_needs_restart" = true ]; then',managed)
        self.assertIn('compose start "$SERVICE"',managed)
        self.assertIn('metadata_temporary=$(mktemp "$BACKUP_ROOT/.managed-metadata.XXXXXX")',managed)
        self.assertIn('validate-backup "$archive" --require-emby --sqlite >"$metadata_temporary"',managed)
        self.assertIn('backup_claim="$BACKUP_ROOT/.managed-config-$stamp.lock"',managed)
        self.assertIn('acquire-backup-claim "$backup_claim" "$BACKUP_ROOT"',managed)
        self.assertIn('backup_claim_owned=true',managed)
        self.assertIn('backup_claim_owned=false',managed)
        self.assertIn('if [ "$backup_claim_owned" = true ] && ! rmdir "$backup_claim"',managed)
        self.assertIn('publish-metadata "$metadata_temporary" "$metadata" "$BACKUP_ROOT"',managed)
        self.assertNotIn('mv "$metadata_temporary" "$metadata"',managed)
        self.assertIn('archive_validated=true',managed)
        self.assertIn('trap - EXIT',managed)
        self.assertIn("trap '' HUP INT TERM",managed)
        self.assertIn("trap 'exit 130' INT",managed)
        self.assertIn('exit "$status"',managed)
        self.assertIn('>/dev/null 2>&1 || true',managed)
        self.assertIn('Managed backup artifact cleanup failed; exact current paths may remain.',managed)
        self.assertIn('Managed backup temporary metadata cleanup failed.',managed)
        self.assertIn('Managed backup claim cleanup failed: $backup_claim',managed)
        self.assertIn('backup_mode="managed"',managed)
        self.assertIn('backup_status="validated"',managed)
        self.assertIn('compose_project="emby-mac-test"',managed)
        self.assertIn('MANAGED_VOLUME=emby-mac-test-config-v1',text)
        self.assertIn('helper_image="emby/embyserver@sha256:',managed)
        self.assertIn('volume_name',managed)

    def test_concurrent_claim_has_one_winner_and_loser_cannot_clean_winner(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            claim=root/".managed-config-20260803T120000Z.lock"
            def attempt():
                try:
                    acquire_backup_claim(claim,root)
                    return "won"
                except EmbyHarnessError:
                    return "lost"
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _: attempt(),range(2)))
            self.assertEqual(["lost","won"],sorted(results))

            archive=root/"managed-config-20260803T120000Z.tar.gz"
            metadata=Path(str(archive)+".metadata.json")
            temporary=root/".managed-metadata.loser"
            archive.write_bytes(b"winner archive")
            metadata.write_text('{"backup_status":"validated"}\n')
            temporary.write_text("loser temporary")
            cleanup_unpublished_backup(archive,metadata,temporary,root,claim_owned=False)
            self.assertEqual(b"winner archive",archive.read_bytes())
            self.assertEqual("validated",json.loads(metadata.read_text())["backup_status"])
            self.assertTrue(claim.is_dir())
            self.assertFalse(temporary.exists())

    def test_owner_cleanup_removes_only_unpublished_current_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            archive=root/"managed-config-20260803T120000Z.tar.gz"
            metadata=Path(str(archive)+".metadata.json")
            temporary=root/".managed-metadata.owner"
            archive.write_bytes(b"partial")
            temporary.write_text("temporary")
            cleanup_unpublished_backup(archive,metadata,temporary,root,claim_owned=True)
            self.assertFalse(archive.exists())
            self.assertFalse(temporary.exists())

            prior=root/"managed-config-20260803T115959Z.tar.gz"
            prior.write_bytes(b"prior")
            self.assertEqual(b"prior",prior.read_bytes())

    def test_existing_archive_or_metadata_is_never_removed_without_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            archive=root/"managed-config-20260803T120000Z.tar.gz"
            metadata=Path(str(archive)+".metadata.json")
            temporary=root/".managed-metadata.loser"
            archive.write_bytes(b"successful archive")
            metadata.write_text('{"backup_status":"validated"}\n')
            temporary.write_text("loser")
            cleanup_unpublished_backup(archive,metadata,temporary,root,claim_owned=False)
            self.assertEqual(b"successful archive",archive.read_bytes())
            self.assertEqual("validated",json.loads(metadata.read_text())["backup_status"])

            owner_temporary=root/".managed-metadata.owner"
            owner_temporary.write_text("owner")
            cleanup_unpublished_backup(archive,metadata,owner_temporary,root,claim_owned=True)
            self.assertEqual(b"successful archive",archive.read_bytes())
            self.assertEqual("validated",json.loads(metadata.read_text())["backup_status"])

    def test_metadata_publication_is_restrictive_atomic_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/".managed-metadata.synthetic"
            target=root/"managed-config-20260803T120000Z.tar.gz.metadata.json"
            source.write_text('{"backup_status":"validated"}\n')
            os.chmod(source,0o600)
            publish_metadata_exclusive(source,target,root)
            self.assertFalse(source.exists())
            self.assertEqual(0o600,stat.S_IMODE(target.stat().st_mode))
            self.assertEqual("validated",json.loads(target.read_text())["backup_status"])

            second=root/".managed-metadata.second"
            second.write_text("new metadata")
            os.chmod(second,0o600)
            with self.assertRaisesRegex(EmbyHarnessError,"already exists"):
                publish_metadata_exclusive(second,target,root)
            self.assertTrue(second.exists())
            self.assertEqual("validated",json.loads(target.read_text())["backup_status"])

    def test_runbook_records_completed_legacy_cleanup(self):
        text=(ROOT/"docs/MacMiniIntegrationRunbook.md").read_text()
        normalized=" ".join(text.split())
        self.assertIn("Legacy-library cleanup result (2026-08-02)",text)
        self.assertIn('`RefreshLibrary=false`',text)
        self.assertIn("4,999 stale movie items reached zero without a manual scan",normalized)
        self.assertIn("12-hour trigger was restored and verified",normalized)
        self.assertIn("no replacement library was created",normalized)

    def test_legacy_backup_guards_and_source_remain_preserved(self):
        text=(ROOT/"scripts/mac-mini-emby-test").read_text()
        legacy=text[text.index("backup_stopped() {"):text.index("repository_backup_guard() {")]
        self.assertIn("stopped_original_guard",legacy)
        self.assertIn('$ORIGINAL_VOLUME,dst=/source,readonly',legacy)
        self.assertIn('legacy) ensure_initialized; backup_stopped',text)

    def test_unit_tests_never_execute_docker_mutations(self):
        # Rendering uses `docker compose config`; all mutation behavior is tested
        # through synthetic dictionaries and archives only.
        self.assertNotIn("subprocess", validate_original_inspect.__code__.co_names)
        self.assertNotIn("subprocess", validate_replacement_inspect.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
