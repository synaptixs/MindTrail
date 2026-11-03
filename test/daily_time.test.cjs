const test=require('node:test'),assert=require('node:assert/strict');
const clock=require('../src/static/interval_time.js');
test('repeated New York hour formats with two explicit offsets and exact milliseconds',()=>{
  const a=Date.parse('2026-11-01T05:30:00.123Z'),b=a+3600000;
  assert.equal(clock.input(a),'2026-11-01T01:30:00.123');assert.equal(clock.input(b),clock.input(a));
  assert.equal(clock.offset(a),'-04:00');assert.equal(clock.offset(b),'-05:00');
  assert.notEqual(clock.stamp(a),clock.stamp(b));
  const choices=[{offset:'-04:00',ms:a},{offset:'-05:00',ms:b}];
  assert.throws(()=>clock.choose(choices,''),/Choose/);assert.equal(clock.choose(choices,'-05:00').ms,b);
});
test('epoch handle geometry preserves pinned selection while collection bounds grow',()=>{
  const old={start:1000,end:10000},pin=clock.move(old,null,'start',3000);
  assert.deepEqual(pin,{start:3000,end:10000});
  assert.equal(clock.move({start:1000,end:10000},{start:3123,end:9000},'start',4123,false).start,4123);
  const grown={start:500,end:20000};assert.deepEqual(clock.move(grown,pin,'end',11000),{start:3000,end:11000});
  assert.deepEqual(pin,{start:3000,end:10000});
  assert.equal(clock.move({start:1000,end:10000},{start:3123,end:9000},'start',4123,false).start,4123);
  assert.deepEqual(clock.move(old,null,'start',99999),{start:9999,end:10000});
  assert.deepEqual(clock.move(old,pin,'end',-10),{start:3000,end:3001});
  assert.equal(clock.move(old,null,'start',3210.5,false).start,3211);
});

test('browser-normalized whole minutes retain seconds for the canonical resolver',()=>{
  assert.equal(clock.wallValue('2026-11-03T09:02'),'2026-11-03T09:02:00');
  assert.equal(clock.wallValue('2026-11-03T09:02:00.123'),'2026-11-03T09:02:00.123');
});
